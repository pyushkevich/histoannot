DROP TABLE IF EXISTS label;
DROP TABLE IF EXISTS labelset;

CREATE TABLE labelset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT
);

CREATE TABLE label (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  labelset INTEGER NOT NULL,
  parent_id INTEGER,
  name TEXT NOT NULL,
  description TEXT,
  color TEXT NOT NULL,
  FOREIGN KEY (labelset) REFERENCES labelset(id),
  FOREIGN KEY (parent_id) REFERENCES label(id),
  UNIQUE (labelset, name)
);

DROP TABLE IF EXISTS training_sample;
CREATE TABLE training_sample (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  label INTEGER NOT NULL,
  slide INTEGER NOT NULL,
  task INTEGER NOT NULL,
  meta_id INTEGER NOT NULL,
  have_patch BOOLEAN DEFAULT FALSE NOT NULL,
  FOREIGN KEY (label) REFERENCES label(id),
  FOREIGN KEY (slide) REFERENCES slide(id),
  FOREIGN KEY (task) REFERENCES task(id),
  FOREIGN KEY (meta_id) REFERENCES edit_meta(id)
);

DROP TABLE IF EXISTS project_labelset;
CREATE TABLE project_labelset (
  project TEXT NOT NULL,
  labelset_name TEXT NOT NULL,
  labelset_id INT NOT NULL,
  PRIMARY KEY(project,labelset_name),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(labelset_name) REFERENCES labelset(name),
  FOREIGN KEY(labelset_id) REFERENCES labelset(id)
);

DROP TABLE IF EXISTS sampling_roi;
CREATE TABLE sampling_roi (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  label INTEGER NOT NULL,
  slide INTEGER NOT NULL,
  task INTEGER NOT NULL,
  meta_id INTEGER NOT NULL,
  json BLOB,
  FOREIGN KEY (label) REFERENCES label(id),
  FOREIGN KEY (slide) REFERENCES slide(id),
  FOREIGN KEY (task) REFERENCES task(id),
  FOREIGN KEY (meta_id) REFERENCES edit_meta(id)
);



