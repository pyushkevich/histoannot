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
