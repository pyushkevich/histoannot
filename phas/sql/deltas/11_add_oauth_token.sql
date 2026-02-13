DROP TABLE IF EXISTS oauth_token;
CREATE TABLE oauth_token (
  user INTEGER NOT NULL,
  authority TEXT NOT NULL,
  oauth_id TEXT NOT NULL,
  access_token TEXT,
  refresh_token TEXT,
  PRIMARY KEY(user, authority),
  FOREIGN KEY(user) REFERENCES user(id)
);

ALTER TABLE user ADD COLUMN oauth_only BOOLEAN NOT NULL DEFAULT(0);