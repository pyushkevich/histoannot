DROP TABLE IF EXISTS user_api_key;
CREATE TABLE user_api_key (
  api_key TEXT PRIMARY KEY,
  user INT NOT NULL,
  t_expires INTEGER NOT NULL,
  FOREIGN KEY(user) REFERENCES user(id)
);