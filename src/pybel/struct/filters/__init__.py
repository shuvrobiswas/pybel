# -*- coding: utf-8 -*-

"""

This module contains functions for filtering node and edge iterables. It relies heavily on the concepts of
`functional programming <https://en.wikipedia.org/wiki/Functional_programming>`_ and the concept of
`predicates <https://en.wikipedia.org/wiki/Predicate_(mathematical_logic)>`_.

"""

from . import edge_filters, edge_predicates, node_filters, node_predicates
from .edge_filters import *
from .edge_predicates import *
from .node_filters import *
from .node_predicates import *

__all__ = (
    node_filters.__all__ +
    edge_filters.__all__ +
    edge_predicates.__all__ +
    node_predicates.__all__
)
