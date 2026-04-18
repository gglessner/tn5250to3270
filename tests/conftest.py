import pathlib
import pytest

VECTORS = pathlib.Path(__file__).parent / "vectors"

@pytest.fixture
def vectors():
    return VECTORS
