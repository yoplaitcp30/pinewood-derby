import pytest
from pinewood_derby.database import Database
from pinewood_derby.service import RaceService


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / 'race.sqlite3')
    yield RaceService(db)
    db.close()
