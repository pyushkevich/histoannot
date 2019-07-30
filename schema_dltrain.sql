DROP TABLE IF EXISTS label;
DROP TABLE IF EXISTS labelset;
DROP TABLE IF EXISTS training_sample;

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

CREATE TABLE training_sample (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tstamp INTEGER NOT NULL,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  label INTEGER NOT NULL,
  slide INTEGER NOT NULL,
  FOREIGN KEY (label) REFERENCES label(id),
  FOREIGN KEY (slide) REFERENCES slide(id)
);
  
INSERT INTO labelset (name) values ('tangles');
INSERT INTO labelset (name) values ('grey_white');
INSERT INTO label (name, labelset, color) values ('tangle',1,'#ff0000');
INSERT INTO label (name, labelset, color) values ('background',1,'#4444ff');





