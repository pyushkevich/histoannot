ALTER TABLE task ADD COLUMN disabled BOOLEAN DEFAULT(0) NOT NULL;

/* Table to reference another task from a task */
DROP TABLE IF EXISTS task_ref;
CREATE TABLE task_ref (
  task INTEGER NOT NULL,
  referenced_task INTEGER NOT NULL,
  PRIMARY KEY (task),
  FOREIGN KEY (task) REFERENCES task (id),
  FOREIGN KEY (referenced_task) REFERENCES task (id)
);