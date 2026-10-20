"""Additive record epochs detect changes even when a value is later restored."""
from groupflow.core.db import register_schema

register_schema('reliability_v1', """
CREATE TABLE IF NOT EXISTS record_epochs (
  record_type TEXT NOT NULL,
  record_id TEXT NOT NULL,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  PRIMARY KEY (record_type, record_id)
);
CREATE TRIGGER IF NOT EXISTS bring_epoch_insert AFTER INSERT ON bring_items BEGIN
  INSERT INTO record_epochs VALUES ('bring_items',NEW.id,NEW.plan_id,1)
  ON CONFLICT(record_type,record_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS bring_epoch_update AFTER UPDATE ON bring_items BEGIN
  INSERT INTO record_epochs VALUES ('bring_items',NEW.id,NEW.plan_id,1)
  ON CONFLICT(record_type,record_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS bring_epoch_delete AFTER DELETE ON bring_items BEGIN
  UPDATE record_epochs SET version=version+1 WHERE record_type='bring_items' AND record_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS expense_epoch_insert AFTER INSERT ON expenses BEGIN
  INSERT INTO record_epochs VALUES ('expenses',NEW.id,NEW.plan_id,1)
  ON CONFLICT(record_type,record_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS expense_epoch_update AFTER UPDATE ON expenses BEGIN
  INSERT INTO record_epochs VALUES ('expenses',NEW.id,NEW.plan_id,1)
  ON CONFLICT(record_type,record_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS expense_epoch_delete AFTER DELETE ON expenses BEGIN
  UPDATE record_epochs SET version=version+1 WHERE record_type='expenses' AND record_id=OLD.id;
END;
""")
