DROP TABLE IF EXISTS user_task_slide_preferences;
CREATE TABLE user_task_slide_preferences (
    user INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    slide INTEGER NOT NULL,
    json TEXT,
    PRIMARY KEY (user, task_id, slide),
    FOREIGN KEY (user) REFERENCES user (id),
    FOREIGN KEY (task_id) REFERENCES task(id),
    FOREIGN KEY (slide) REFERENCES slide(id)
);