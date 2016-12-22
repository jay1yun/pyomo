#  _________________________________________________________________________
#
#  PyUtilib: A Python utility library.
#  Copyright (c) 2008 Sandia Corporation.
#  This software is distributed under the BSD License.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  _________________________________________________________________________

__all__ = ("sos",
           "sos1",
           "sos2",
           "sos_list",
           "sos_dict")

import abc

import pyutilib.math

from pyomo.core.base.component_interface import \
    (IComponent,
     _IActiveComponent,
     _IActiveComponentContainer,
     _abstract_readwrite_property,
     _abstract_readonly_property)
from pyomo.core.base.component_dict import ComponentDict
from pyomo.core.base.component_list import ComponentList
from pyomo.core.base.numvalue import (NumericValue,
                                      potentially_variable)

import six
from six.moves import zip

class ISOS(IComponent, _IActiveComponent):
    """
    The interface for Special Ordered Sets.
    """
    __slots__ = ()

    #
    # Implementations can choose to define these
    # properties as using __slots__, __dict__, or
    # by overriding the @property method
    #

    variables = _abstract_readwrite_property(
        doc="The sos variables")
    weights = _abstract_readwrite_property(
        doc="The sos variables")
    level = _abstract_readwrite_property(
        doc="The sos level (e.g., 1,2,...)")

    #
    # Interface
    #

    def items(self):
        """Iterator over the sos variables and weights as tuples"""
        return zip(self.variables, self.weights)

    def __contains__(self, v):
        """Check if the sos contains the variable v"""
        for x in self.variables:
            if id(x) == id(v):
                return True

    def __len__(self):
        """The number of members in the set"""
        return len(self.variables)

class sos(ISOS):
    """A Special Ordered Set of type n."""
    # To avoid a circular import, for the time being, this
    # property will be set in sos.py
    _ctype = None
    __slots__ = ("_parent",
                 "_active",
                 "_variables",
                 "_weights",
                 "_level",
                 "__weakref__")
    def __init__(self, variables, weights=None, level=1):
        self._parent = None
        self._active = True
        self._variables = tuple(variables)
        self._weights = None
        self._level = level
        if weights is None:
            self._weights = tuple(range(1,len(self._variables)+1))
        else:
            self._weights = tuple(weights)
            for w in self._weights:
                if potentially_variable(w):
                    raise ValueError(
                        "Weights for Special Ordered Sets must be "
                        "expressions restricted to data")

        assert len(self._variables) == len(self._weights)
        assert self._level >= 1

    #
    # Define the ISOS abstract methods
    #

    @property
    def variables(self): return self._variables
    @property
    def weights(self): return self._weights
    @property
    def level(self): return self._level

def sos1(variables, weights=None):
    """A Special Ordered Set of type 1.

    This is an alias for sos(..., level=1)"""
    return sos(variables, weights=weights, level=1)

def sos2(variables, weights=None):
    """A Special Ordered Set of type 2.

    This is an alias for sos(..., level=2).
    """
    return sos(variables, weights=weights, level=2)

class sos_list(ComponentList,
               _IActiveComponentContainer):
    """A list-style container for Special Ordered Sets."""
    # To avoid a circular import, for the time being, this
    # property will be set in sos.py
    _ctype = None
    __slots__ = ("_parent",
                 "_active",
                 "_data")
    if six.PY3:
        # This has to do with a bug in the abc module
        # prior to python3. They forgot to define the base
        # class using empty __slots__, so we shouldn't add a slot
        # for __weakref__ because the base class has a __dict__.
        __slots__ = list(__slots__) + ["__weakref__"]

    def __init__(self, *args, **kwds):
        self._parent = None
        self._active = True
        super(sos_list, self).__init__(*args, **kwds)

class sos_dict(ComponentDict,
               _IActiveComponentContainer):
    """A dict-style container for Special Ordered Sets."""
    # To avoid a circular import, for the time being, this
    # property will be set in sos.py
    _ctype = None
    __slots__ = ("_parent",
                 "_active",
                 "_data")
    if six.PY3:
        # This has to do with a bug in the abc module
        # prior to python3. They forgot to define the base
        # class using empty __slots__, so we shouldn't add a slot
        # for __weakref__ because the base class has a __dict__.
        __slots__ = list(__slots__) + ["__weakref__"]

    def __init__(self, *args, **kwds):
        self._parent = None
        self._active = True
        super(sos_dict, self).__init__(*args, **kwds)