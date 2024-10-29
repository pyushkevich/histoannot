import json

class AccessLevel:

    @staticmethod
    def is_valid(access_level):
        return access_level in ["none", "read", "write", "admin"]

    @staticmethod
    def from_abbrv(abbrv):
        if AccessLevel.is_valid(abbrv):
            return abbrv
        d = {'N': 'none', 'R': 'read', 'W': 'write', 'A': 'admin' }
        return d.get(abbrv.upper(), None)

    @staticmethod
    def to_int(access_level):
        return {"none": 0, "read": 1, "write": 2, "admin": 3}[access_level]

    @staticmethod
    def from_int(access_level):
        return ["none", "read", "write", "admin"][value]

    @staticmethod
    def check_access(test_level, min_level):
        """Check access level against minimum required level"""
        return AccessLevel.to_int(test_level) >= AccessLevel.to_int(min_level)


# A function to return an error code from JSON-based REST API
def abort_json(error_message, error_code = 400):
    return json.dumps({'success': False, 'error': error_message}), error_code, {'ContentType': 'application/json'}

# A function to return success from JSON-based REST API
def success_json():
    return json.dumps({'success': True}), 200, {'ContentType': 'application/json'}

