import pytest
from watermarking_methods.add_after_eof import AddAfterEOF


@pytest.fixture
def toy_eof_method(monkeypatch):
    """Register toy-eof for one test only.

    Production does not register it (it is trivially stripped), but it is fast
    and works on any PDF, so the HTTP and RMAP tests use it as a stand-in.
    """
    import watermarking_utils

    monkeypatch.setitem(watermarking_utils.METHODS, AddAfterEOF.name, AddAfterEOF())
