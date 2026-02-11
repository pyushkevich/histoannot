/* Update project access table */
ALTER TABLE user ADD COLUMN is_group BOOLEAN DEFAULT(0) NOT NULL;

/* Drop and recreate group membership table */
DROP TABLE IF EXISTS group_membership;
CREATE TABLE group_membership (
  group_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  PRIMARY KEY(group_id, user_id),
  FOREIGN KEY(group_id) REFERENCES user(id),
  FOREIGN KEY(user_id) REFERENCES user(id)
);