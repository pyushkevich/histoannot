DROP TABLE IF EXISTS label;
DROP TABLE IF EXISTS labelset;

CREATE TABLE labelset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
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
  
INSERT INTO labelset (name) values ('tangles');
INSERT INTO labelset (name) values ('grey_white');
INSERT INTO label (name, labelset, color) values ('tangle',1,'#ff0000');
INSERT INTO label (name, labelset, color) values ('background',1,'#4444ff');





