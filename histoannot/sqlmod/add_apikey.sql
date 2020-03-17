DROP TABLE IF EXISTS api_key;
CREATE TABLE api_key (
  user INTEGER NOT NULL,
  key TEXT NOT NULL,
  t_expires INTEGER NOT NULL,
  PRIMARY KEY(user),
  FOREIGN KEY (user) REFERENCES user (id)
);

