#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________
"""
This module contains a library of transformations for
representing piecewise linear constraints on a Pyomo
model. All piecewise representations except for 'sos2' and
'bigm' were taken from the following paper:

Mixed-Integer Models for Non-separable Piecewise Linear
Optimization: Unifying framework and Extensions (Vielma,
Nemhauser 2008).
"""

# ****** NOTE: Nothing in this file relies on integer division *******
#              I predict this will save numerous headaches as
#              well as gratuitous calls to float() in this code
from __future__ import division

# TODO: Figure out of the 'log' and 'dlog' representations
# really do require (2^n)+1 points or if there is a way to
# handle the between sizes.

__all__ = ("piecewise",)

import pyutilib.enum

from pyomo.core.base.numvalue import value
from pyomo.core.base.set_types import Binary
from pyomo.core.base.component_block import (block, StaticBlock)
from pyomo.core.base.component_expression import expression
from pyomo.core.base.component_variable import (variable_list,
                                                variable_dict,
                                                variable)
from pyomo.core.base.component_constraint import (constraint,
                                                  constraint_list,
                                                  linear_constraint)
from pyomo.core.base.component_sos import sos2
from pyomo.core.base.component_piecewise.util import \
    (is_nondecreasing,
     is_postive_power_of_two,
     log2floor,
     generate_gray_code)

import six
from six.moves import xrange, zip

registered_transforms = {}

#TODO: (simplify,
#       warning_tol,
#       warning_domain_coverage,
#       unbounded_domain_var,
def piecewise(breakpoints,
              values,
              input=None,
              output=None,
              bound='eq',
              repn='sos2'):
    """
    Transforms a list of breakpoints and values into a mixed-integer
    representation of a piecewise function.

    Args:
        breakpoints: The list of breakpoints of the
            piecewise linear function. This can be a list of
            number or a list of objects that store mutable
            data (e.g., mutable parameters). It is assumed
            that the points in this list are in
            non-decreasing order.
        values: The list of values of the piecewise linear
            function at each of the breakpoints. This list
            must be the same length as the breakpoints
            argument.
        input: The variable constrained to be the input of
            the piecewise linear function.
        output: The variable constrained to be the output of
            the piecewise linear function.
        bound: The type of bound on the output to
            generate. Can be one of:
                - 'lb': y <= f(x)
                - 'eq': y  = f(x)
                - 'ub': y >= f(x)
        repn: The type of piecewise representation to
            use. Can be one of:
                - 'sos2': standard representation using sos2 constraints (+)
                -  'dcc': disaggregated convex combination (*+)
                - 'dlog': logarithmic disaggregated convex combination (*+)
                -   'cc': convex combination (*+)
                -  'log': logarithmic branching convex combination (*+)
                -   'mc': multiple choice (*)
                -  'inc': incremental method (*+)
           + supports step functions
           * source: "Mixed-Integer Models for Non-separable
                      Piecewise Linear Optimization:
                      Unifying framework and Extensions"
                      (Vielma, Nemhauser 2008)

    Returns: A block containing the necessary auxiliary
        variables and constraints that enforce the piecewise
        linear relationship between the input and output
        variable.
    """
    transorm = None
    try:
        transform = registered_transforms[repn]
    except KeyError:
        raise ValueError(
            "Keyword assignment repn='%s' is not valid. "
            "Must be one of: %s"
            % (repn,
               str(sorted(registered_transforms.keys()))))
    assert transform is not None

    return transform(breakpoints,
                     values,
                     input=input,
                     output=output,
                     bound=bound)

class _PiecewiseLinearFunction(StaticBlock):
    """
    A piecewise linear function defined by a list of
    breakpoints and values.

    Assumes the breakpoints are in nondecreasing order, but this
    is not validated because the list of breakpoints and values
    can be expressions (e.g., mutable parameters).
    """
    __slots__ = ("_input", "_output", "_breakpoints", "_values")

    def __init__(self,
                 breakpoints,
                 values,
                 input=None,
                 output=None):
        super(_PiecewiseLinearFunction, self).__init__()
        self._input = expression()
        self._output = expression()
        self._breakpoints = breakpoints
        self._values = values
        if type(self._breakpoints) is not tuple:
            self._breakpoints = tuple(self._breakpoints)
        if type(self._values) is not tuple:
            self._values = tuple(self._values)
        # call the setters
        self.set_input(input)
        self.set_output(output)
        #if not is_nondecreasing(self._breakpoints):
        #    raise ValueError(
        #        "The list of breakpoints is not nondecreasing: %s"
        #        % (str(self._breakpoints)
        if len(self._breakpoints) != len(self._values):
            raise ValueError(
                "The number of breakpoints (%s) differs from "
                "the number of function values (%s)"
                % (len(self._breakpoints), len(self._values)))

    @property
    def input(self):
        return self._input
    def set_input(self, input):
        self._input.expr = input

    @property
    def output(self):
        return self._output
    def set_output(self, output):
        self._output.expr = output

    @property
    def breakpoints(self):
        """The set of breakpoints used to defined this function"""
        return self._breakpoints

    @property
    def values(self):
        """The set of values used to defined this function"""
        return self._values

    def __call__(self, x):
        # Note: One could implement binary search here to
        #       speed this up. I don't see this
        #       functionality being used very often (and the
        #       list of breakpoints probably isn't too
        #       large), so I'm doing it the easy way.
        for i in xrange(len(self.breakpoints)-1):
            xL = value(self.breakpoints[i])
            xU = value(self.breakpoints[i+1])
            assert xL <= xU
            if (xL <= x) and (x <= xU):
                yL = value(self.values[i])
                if xL == xU: # a step function
                    return yL
                yU = value(self.values[i+1])
                return yL + (float(yU-yL)/(xU-xL))*(x-xL)
        raise ValueError("The point %s is outside of the "
                         "function domain: [%s,%s]."
                         % (x,
                            value(self.breakpoints[0]),
                            value(self.breakpoints[-1])))

class piecewise_sos2(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the SOS2 formulation
    """
    __slots__ = ("_y", "_c1", "_c2", "_c3", "_c4")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_sos2, self).__init__(*args, **kwds)

        # create vars
        y = self._y = variable_list(
            variable(lb=0) for i in xrange(len(self.breakpoints)))
        y_tuple = tuple(y)

        # create piecewise constraints
        self._c1 = linear_constraint(
             variables=y_tuple + (self.input,),
             coefficients=self.breakpoints + (-1,),
             rhs=0)

        self._c2 = linear_constraint(
            variables=y_tuple + (self.output,),
            coefficients=self.values + (-1,))
        if bound == 'ub':
            self._c2.lb = 0
        elif bound == 'lb':
            self._c2.ub = 0
        elif bound == 'eq':
            self._c2.rhs = 0
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = linear_constraint(variables=y_tuple,
                                    coefficients=(1,)*len(y),
                                    rhs=1)
        self._c4 = sos2(y)
registered_transforms['sos2'] = piecewise_sos2

class piecewise_dcc(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the DCC formulation
    """
    __slots__ = ("_lmbda", "_y", "_c1", "_c2", "_c3", "_c4")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_dcc, self).__init__(*args, **kwds)

        # create index sets
        polytopes = range(len(self.breakpoints)-1)
        vertices = range(len(self.breakpoints))
        def polytope_verts(p):
            return xrange(p,p+2)

        # create vars
        lmbda = self._lmbda = variable_dict(
            ((p,v), variable(lb=0))
            for p in polytopes
            for v in vertices)
        y = self._y = variable_list(
            variable(domain=Binary)
            for p in polytopes)

        # create piecewise constraints
        self._c1 = linear_constraint(
            variables=tuple(lmbda[p,v]
                            for p in polytopes
                            for v in polytope_verts(p)) + \
                      (self.input,),
            coefficients=tuple(self.breakpoints[v]
                               for p in polytopes
                               for v in polytope_verts(p)) + (-1,),
            rhs=0)

        self._c2 = linear_constraint(
            variables=tuple(lmbda[p,v]
                            for p in polytopes
                            for v in polytope_verts(p)) + \
                      (self.output,),
            coefficients=tuple(self.values[v]
                               for p in polytopes
                               for v in polytope_verts(p)) + (-1,))
        if bound == 'ub':
            self._c2.lb = 0
        elif bound == 'lb':
            self._c2.ub = 0
        elif bound == 'eq':
            self._c2.rhs = 0
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = constraint_list()
        for p in polytopes:
            variables = tuple(lmbda[p,v] for v in polytope_verts(p))
            self._c3.append(
                linear_constraint(
                    variables=variables + (y[p],),
                    coefficients=(1,)*len(variables) + (-1,),
                    rhs=0))

        self._c4 = linear_constraint(
            variables=tuple(y),
            coefficients=(1,)*len(y),
            rhs=1)
registered_transforms['dcc'] = piecewise_dcc

class piecewise_cc(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the CC formulation
    """
    __slots__ = ("_lmbda", "_y", "_c1", "_c2", "_c3", "_c4", "_c5")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_cc, self).__init__(*args, **kwds)

        # create index sets
        polytopes = range(len(self.breakpoints)-1)
        vertices = range(len(self.breakpoints))
        def vertex_polys(v):
            if v == 0:
                return [v]
            if v == len(self.breakpoints)-1:
                return [v-1]
            else:
                return [v-1,v]

        # create vars
        lmbda = self._lmbda = variable_list(
            variable(lb=0) for v in vertices)
        y = self._y = variable_list(
            variable(domain=Binary)
            for p in polytopes)

        lmbda_tuple = tuple(lmbda)

        # create piecewise constraints
        self._c1 = linear_constraint(
            variables=lmbda_tuple + (self.input,),
            coefficients=self.breakpoints + (-1,),
            rhs=0)

        self._c2 = linear_constraint(
            variables=lmbda_tuple + (self.output,),
            coefficients=self.values + (-1,))
        if bound == 'ub':
            self._c2.lb = 0
        elif bound == 'lb':
            self._c2.ub = 0
        elif bound == 'eq':
            self._c2.rhs = 0
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = linear_constraint(
            variables=lmbda_tuple,
            coefficients=(-1,)*len(lmbda),
            rhs=1)

        self._c4 = constraint_list()
        for v in vertices:
            variables = tuple(y[p] for p in vertex_polys(v))
            self._c4.append(linear_constraint(
                variables=variables + (lmbda[v],),
                coefficients=(1,)*len(variables) + (-1,),
                lb=0))

        self._c5 = linear_constraint(
            variables=tuple(y),
            coefficients=(1,)*len(y),
            rhs=1)
registered_transforms['cc'] = piecewise_cc

class piecewise_mc(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the MC formulation
    """
    __slots__ = ("_lmbda", "_y", "_c1", "_c2", "_c3", "_c4", "_c5")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_mc, self).__init__(*args, **kwds)

        # create indexers
        polytopes = range(len(self.breakpoints)-1)

        # create constants (using future division)
        # these might also be expressions if the breakpoints
        # or values lists contain mutable objects
        slopes = tuple((self.values[p+1] - self.values[p]) / \
                       (self.breakpoints[p+1] - self.breakpoints[p])
                       for p in polytopes)
        intercepts = tuple(self.values[p] - \
                           (slopes[p] * self.breakpoints[p])
                           for p in polytopes)

        # create vars
        lmbda = self._lmbda = variable_list(variable()
                                    for p in polytopes)
        lmbda_tuple = tuple(lmbda)
        y = self._y = variable_list(variable(domain=Binary)
                                    for p in polytopes)
        y_tuple = tuple(y)

        # create piecewise constraints
        self._c1 = linear_constraint(
            variables=lmbda_tuple + (self.input,),
            coefficients=(1,)*len(lmbda) + (-1,),
            rhs=0)

        self._c2 = linear_constraint(
            variables=lmbda_tuple + y_tuple + (self.output,),
            coefficients=slopes + intercepts + (-1,))
        if bound == 'ub':
            self._c2.lb = 0
        elif bound == 'lb':
            self._c2.ub = 0
        elif bound == 'eq':
            self._c2.rhs = 0
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = constraint_list()
        self._c4 = constraint_list()
        for p in polytopes:
            self._c3.append(linear_constraint(
                variables=(y[p], lmbda[p]),
                coefficients=(self.breakpoints[p], -1),
                ub=0))
            self._c4.append(linear_constraint(
                variables=(lmbda[p], y[p]),
                coefficients=(1, self.breakpoints[p+1]),
                ub=0))

        self._c5 = linear_constraint(
            variables=y_tuple,
            coefficients=(1,)*len(y),
            rhs=1)
registered_transforms['mc'] = piecewise_mc

class piecewise_inc(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the INC formulation
    """
    __slots__ = ("_delta", "_y", "_c1", "_c2", "_c3", "_c4")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_inc, self).__init__(*args, **kwds)

        # create indexers
        polytopes = range(len(self.breakpoints)-1)

        # create vars
        delta = self._delta = variable_list(
            variable() for p in polytopes)
        delta[0].ub = 1
        delta[-1].lb = 0
        delta_tuple = tuple(delta)
        y = self._y = variable_list(
            variable(domain=Binary) for p in polytopes[:-1])

        # create piecewise constraints
        self._c1 = linear_constraint(
            variables=(self.input,) + delta_tuple,
            coefficients=(-1,) + tuple(self.breakpoints[p+1] - \
                                       self.breakpoints[p]
                                       for p in polytopes),
            rhs=-self.breakpoints[0])

        self._c2 = linear_constraint(
            variables=(self.output,) + delta_tuple,
            coefficients=(-1,) + tuple(self.values[p+1] - \
                                       self.values[p]
                                       for p in polytopes))
        if bound == 'ub':
            self._c2.lb = -self.values[0]
        elif bound == 'lb':
            self._c2.ub = -self.values[0]
        elif bound == 'eq':
            self._c2.rhs = -self.values[0]
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = constraint_list()
        self._c4 = constraint_list()
        for p in polytopes[:-1]:
            self._c3.append(linear_constraint(
                variables=(delta[p+1], y[p]),
                coefficients=(1, -1),
                ub=0))
            self._c4.append(linear_constraint(
                variables=(y[p], delta[p]),
                coefficients=(1, -1),
                ub=0))
registered_transforms['inc'] = piecewise_inc

class piecewise_dlog(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the DLOG formulation
    """
    __slots__ = ("_lmbda", "_y", "_c1", "_c2", "_c3", "_c4", "_c5")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_dlog, self).__init__(*args, **kwds)

        if not is_postive_power_of_two(len(self.breakpoints)-1):
            raise ValueError("The list of breakpoints must be "
                             "of length (2^n)+1 for some positive "
                             "integer n. Invalid length: %s"
                             % (len(self.breakpoints)))

        # create branching schemes
        L = log2floor(len(self.breakpoints)-1)
        assert 2**L == len(self.breakpoints)-1
        B_ZERO,B_ONE = self._branching_scheme(L)

        # create indexers
        polytopes = range(len(self.breakpoints)-1)
        vertices = range(len(self.breakpoints))
        def polytope_verts(p):
            return xrange(p,p+2)

        # create vars
        lmbda = self._lmbda = variable_dict(
            ((p,v), variable(lb=0))
            for p in polytopes
            for v in vertices)
        y = self._y = variable_list(
            variable(domain=Binary)
            for i in range(L))

        # create piecewise constraints
        self._c1 = linear_constraint(
            variables=(self.input,) + tuple(lmbda[p,v]
                                            for p in polytopes
                                            for v in polytope_verts(p)),
            coefficients=(-1,) + tuple(self.breakpoints[v]
                                       for p in polytopes
                                       for v in polytope_verts(p)),
            rhs=0)

        self._c2 = linear_constraint(
            variables=(self.output,) + tuple(lmbda[p,v]
                                             for p in polytopes
                                             for v in polytope_verts(p)),
            coefficients=(-1,) + tuple(self.values[v]
                                       for p in polytopes
                                       for v in polytope_verts(p)))
        if bound == 'ub':
            self._c2.lb = 0
        elif bound == 'lb':
            self._c2.ub = 0
        elif bound == 'eq':
            self._c2.rhs = 0
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = linear_constraint(
            variables=tuple(lmbda.values()),
            coefficients=(1,)*len(lmbda),
            rhs=1)

        self._c4 = constraint_list()
        for i in range(L):
            variables = tuple(lmbda[p,v]
                              for p in B_ZERO[i]
                              for v in polytope_verts(p))
            self._c4.append(linear_constraint(
                variables=variables + (y[i],),
                coefficients=(1,)*len(variables) + (-1,),
                ub=0))

        self._c5 = constraint_list()
        for i in range(L):
            variables = tuple(lmbda[p,v]
                              for p in B_ONE[i]
                              for v in polytope_verts(p))
            self._c5.append(linear_constraint(
                variables=variables + (y[i],),
                coefficients=(1,)*len(variables) + (1,),
                ub=1))

    def _branching_scheme(self, L):
        N = 2**L
        mylists1 = []
        for i in range(L):
            mylists1.append([])
            start = 0
            step = int(N/(2**i))
            while start < N:
                mylists1[i].extend(j for j in xrange(start,start+step))
                start += 2*step

        biglist = range(N)
        mylists2 = []
        for i in range(len(mylists1)):
            tmp = []
            for j in biglist:
                if j not in mylists1[i]:
                    tmp.append(j)
            mylists2.append(sorted(tmp))

        return mylists1, mylists2
registered_transforms['dlog'] = piecewise_dlog


class piecewise_log(_PiecewiseLinearFunction):
    """
    Expresses a piecewise linear function using
    the LOG formulation
    """
    __slots__ = ("_lmbda", "_y", "_c1", "_c2", "_c3", "_c4", "_c5")

    def __init__(self, *args, **kwds):
        bound = kwds.pop('bound', 'eq')
        super(piecewise_log, self).__init__(*args, **kwds)

        if not is_postive_power_of_two(len(self.breakpoints)-1):
            raise ValueError("The list of breakpoints must be "
                             "of length (2^n)+1 for some positive "
                             "integer n. Invalid length: %s"
                             % (len(self.breakpoints)))

        # create branching schemes
        L = log2floor(len(self.breakpoints)-1)
        S,B_LEFT,B_RIGHT = self._branching_scheme(L)

        # create indexers
        polytopes = range(len(self.breakpoints) - 1)
        vertices = range(len(self.breakpoints))

        # create vars
        lmbda = self._lmbda = variable_list(
            variable(lb=0)
            for v in vertices)
        y = self._y = variable_list(variable(domain=Binary)
                                    for s in S)

        # create piecewise constraints
        self._c1 = linear_constraint(
            variables=(self.input,) + tuple(lmbda),
            coefficients=(-1,) + self.breakpoints,
            rhs=0)

        self._c2 = linear_constraint(
            variables=(self.output,) + tuple(lmbda),
            coefficients=(-1,) + self.values)
        if bound == 'ub':
            self._c2.lb = 0
        elif bound == 'lb':
            self._c2.ub = 0
        elif bound == 'eq':
            self._c2.rhs = 0
        else:
            raise ValueError("Invalid bound type %r. Must be "
                             "one of: ['lb','ub','eq']"
                             % (bound))

        self._c3 = linear_constraint(
            variables=tuple(lmbda),
            coefficients=(1,)*len(lmbda),
            rhs=1)

        self._c4 = constraint_list()
        for s in S:
            variables=tuple(lmbda[v] for v in B_LEFT[s])
            self._c4.append(linear_constraint(
                variables=variables + (y[s],),
                coefficients=(1,)*len(variables) + (-1,),
                ub=0))

        self._c5 = constraint_list()
        for s in S:
            variables=tuple(lmbda[v] for v in B_RIGHT[s])
            self._c5.append(linear_constraint(
                variables=variables + (y[s],),
                coefficients=(1,)*len(variables) + (1,),
                ub=1))

    def _branching_scheme(self, n):
        N = 2**n
        S = range(n)
        # turn the GrayCode into a dictionary indexed
        # starting at 1
        G = generate_gray_code(n)

        L = tuple([k for k in xrange(N)
                   if ((k == 0) or (G[k][s] == 1))
                   and ((k == N-1) or (G[k+1][s] == 1))] for s in S)
        R = tuple([k for k in xrange(N)
                   if ((k == 0) or (G[k][s] == 0))
                   and ((k == N-1) or (G[k+1][s] == 0))] for s in S)

        return S, L, R
registered_transforms['log'] = piecewise_log

if False:           #pragma:nocover
    class piecewise_bigm(_PiecewiseLinearFunction):
        """
        Expresses a piecewise linear function using
        the BIGM formulation
        """
        __slots__ = ("_x", "_y", "_c1", "_c2", "_c3", "_c4", "_c5")

        def __init__(self, *args, **kwds):
            bound = kwds.pop('bound', 'eq')
            binary = kwds.pop('binary', True)
            super(piecewise_bigm, self).__init__(*args, **kwds)
            self.binary = binary
            if not (self.binary in [True,False]):
                raise ValueError("_BIGMPiecewise must be initialized with the binary "\
                                  "flag set to True or False (choose one).")

        def construct(self,pblock,input,output):
            # The BIGM methods currently determine tightest possible M
            # values. This method is implemented in such a way that
            # binary/sos1 variables are not created when this M is zero
            tag = ""
            breakpoints = pblock._domain_pts
            values = pblock._range_pts
            bound = pblock._bound
            if None in [breakpoints,values,bound]:
                raise RuntimeError("_BIGMPiecewise: construct() called during "\
                                    "invalid state.")
            len_breakpoints = len(self.breakpoints)

            if self.binary is True:
                tag += "bin"
            else:
                tag += "sos1"

            # generate tightest bigM values
            OPT_M = {}
            OPT_M['UB'] = {}
            OPT_M['LB'] = {}

            if bound in ['ub','eq']:
                OPT_M['UB'] = self._find_M(self.breakpoints, self.values, 'ub')
            if bound in ['lb','eq']:
                OPT_M['LB'] = self._find_M(self.breakpoints, self.values, 'lb')

            all_keys = set(iterkeys(OPT_M['UB'])).union(iterkeys(OPT_M['LB']))
            full_indices = []
            full_indices.extend(range(1,len_breakpoints))
            bigm_y_index = None
            bigm_y = None
            if len(all_keys) > 0:
                bigm_y_index = all_keys

                def y_domain():
                    if self.binary is True:
                        return Binary
                    else:
                        return NonNegativeReals
                setattr(pblock,tag+'_y', Var(bigm_y_index,within=y_domain()))
                bigm_y = getattr(pblock,tag+'_y')

            def con1_rule(model,i):
                if bound in ['ub','eq']:
                    rhs = 1.0
                    if i not in OPT_M['UB']:
                        rhs *= 0.0
                    else:
                        rhs *= OPT_M['UB'][i]*(1-bigm_y[i])
                    # using future division
                    return output - self.values[i-1] - \
                    ((self.values[i]-self.values[i-1])/(self.breakpoints[i]-self.breakpoints[i-1]))*(input-self.breakpoints[i-1])\
                    <= rhs
                elif bound == 'lb':
                    rhs = 1.0
                    if i not in OPT_M['LB']:
                        rhs *= 0.0
                    else:
                        rhs *= OPT_M['LB'][i]*(1-bigm_y[i])
                    # using future division
                    return output - self.values[i-1] - \
                    ((self.values[i]-self.values[i-1])/(self.breakpoints[i]-self.breakpoints[i-1]))*(input-self.breakpoints[i-1])\
                    >= rhs

            def con2_rule(model):
                expr = [bigm_y[i] for i in xrange(1,len_breakpoints) if i in all_keys]
                if len(expr) > 0:
                    return sum(expr) == 1
                else:
                    return Constraint.Skip

            def conAFF_rule(model,i):
                rhs = 1.0
                if i not in OPT_M['LB']:
                    rhs *= 0.0
                else:
                    rhs *= OPT_M['LB'][i]*(1-bigm_y[i])
                # using future division
                return output - self.values[i-1] - \
                ((self.values[i]-self.values[i-1])/(self.breakpoints[i]-self.breakpoints[i-1]))*(input-breakpoints[i-1]) \
                >= rhs

            pblock.BIGM_constraint1 = Constraint(full_indices,rule=con1_rule)
            if len(all_keys) > 0:
                pblock.BIGM_constraint2 = Constraint(rule=con2_rule)
            if bound == 'eq':
                pblock.BIGM_constraint3 = Constraint(full_indices,rule=conAFF_rule)

            if len(all_keys) > 0:
                if self.binary is False:
                    pblock.BIGM_constraint4 = SOSConstraint(var=bigm_y, sos=1)

            # In order to enforce the same behavior as actual piecewise
            # constraints, we constrain the domain variable between the
            # outer domain pts. But in order to prevent filling the model
            # with unecessary constraints, we only do this when absolutely
            # necessary.
            if not input.lb is None and input.lb < self.breakpoints[0]:
                pblock.bigm_domain_constraint_lower = Constraint(expr=self.breakpoints[0] <= input)
            if not input.ub is None and input.ub > self.breakpoints[-1]:
                pblock.bigm_domain_constraint_upper = Constraint(expr=input <= self.breakpoints[-1])

        def _M_func(self,a,Fa,b,Fb,c,Fc):
            # using future division
            return Fa - Fb - ((a-b) * ((Fc-Fb) / (c-b)))

        def _find_M(self,breakpoints,values,bound):
            len_breakpoints = len(breakpoints)
            _self_M_func = self._M_func

            M_final = {}
            for j in xrange(1,len_breakpoints):
                index = j
                if bound == 'lb':
                    M_final[index] = min( [0.0, min([_self_M_func(breakpoints[k],values[k],
                                                                  breakpoints[j-1],values[j-1],
                                                                  breakpoints[j],values[j]) \
                                                for k in xrange(len_breakpoints)])] )
                elif bound == 'ub':
                    M_final[index] = max( [0.0, max([_self_M_func(breakpoints[k],values[k],
                                                                  breakpoints[j-1],values[j-1],
                                                                  breakpoints[j],values[j]) \
                                                 for k in xrange(len_breakpoints)])] )
                else:
                    raise ValueError("Invalid Bound passed to _find_M function")
                if M_final[index] == 0.0:
                    del M_final[index]
            return M_final
    registered_transforms['bigm'] = piecewise_bigm
