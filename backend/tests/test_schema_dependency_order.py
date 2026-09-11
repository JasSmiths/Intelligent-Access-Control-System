import warnings

from sqlalchemy.exc import SAWarning

from app import models  # noqa: F401
from app.db.base import Base


def test_dependency_metadata_sorts_without_ignoring_cyclic_foreign_keys():
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        tables = [table.name for table in Base.metadata.sorted_tables]
    assert tables.index("external_dependencies") < tables.index("dependency_update_analyses")
