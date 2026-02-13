# A schema against which the JSON is validated
task_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 80},
        "desc": {"type": "string", "maxLength": 1024},
        "mode": {"type": "string", "enum": ["annot", "dltrain", "browse", "sampling"]},
        "reference-task": {"type": "string"},
        "dltrain": {
            "type": "object",
            "properties": {
                "labelset": {"type": "string"},
                "min-size": {"type": "integer"},
                "max-size": {"type": "integer"},
                "display-patch-size": {"type": "integer"}
            },
            "required": ["labelset"]
        },
        "sampling": {
            "type": "object",
            "properties": {
                "labelset": {"type": "string"}
            },
            "required": ["labelset"]
        },
        "restrict-access": {"type": "boolean"},
        "download-slide-size-limit": {"type": "integer"},
        "anonymize": {"type": "boolean"},
        "stains": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "tags": {
            "type": "object",
            "properties": {
                "any": {"type": "array", "items": {"type": "string"}},
                "all": {"type": "array", "items": {"type": "string"}},
                "not": {"type": "array", "items": {"type": "string"}}
            }
        },
        "specimens": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },
    "required": ["name", "mode", "restrict-access"]
}

# A schema for importing labels
label_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 80},
            "description": {"type": "string", "maxLength": 1024},
            "color": {"type": "string", "minLength": 2, "maxLength": 80}
        },
        "required": ["name", "color"]
    }
}

# A schema against which the JSON is validated
project_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "disp_name": {"type": "string", "minLength": 2, "maxLength": 80},
        "desc": {"type": "string", "maxLength": 1024},
        "base_url": {"type": "string"},
        "url_schema": {"type": "object"}},
    "required": ["disp_name", "desc", "base_url"]
}

# A schema against which to validate per-slide JSON files
# A schema against which the JSON is validated
slide_json_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "specimen": {"type": "string" },
        "block": {"type": "string"},
        "stain": {"type": "string"},
        "section": {"type": "integer"},
        "slide_number": {"type": "integer"},
        "cert": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [ "specimen", "block", "stain" ]
}

# Schema for user preferences json
user_preferences_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "rotation": {"type": "number"},
        "flip": {"type": "boolean"}
    }
}


