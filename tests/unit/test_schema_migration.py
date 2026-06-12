from orc.paths import workspace_db_path
from orc.storage import db
from orc.storage import workspace as ws_module


def test_existing_v1_workspace_gains_gold_tables_on_resolve(orc_home, monkeypatch) -> None:
    # Create at v1 by forcing the old version, then resolve under v2 code.
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
    ws_module.create("legacy")
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    ws_module.resolve("legacy")  # must migrate

    with db.open_connection(workspace_db_path("legacy")) as conn:
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"gold_claim", "eval_run", "tiered_policy"} <= names
        ver = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        assert ver == "2"
