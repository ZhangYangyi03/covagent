"""covagent -- coverage closure: read the report, find the dark bins, close them.

The unit of work is not "generate tests". It is: a specific line of RTL did not
execute, here is why, here is the stimulus that would reach it, prove the number
went up. Everything else in this package is plumbing for that sentence.
"""

__version__ = "0.1.0"

from . import coverage, holes, sim  # noqa: F401

__all__ = ["coverage", "holes", "sim"]
