class AccessLevel:

    @staticmethod
    def is_valid(access_level):
        return access_level in ["none", "read", "write", "admin"]

    @staticmethod
    def to_int(access_level):
        return {"none": 0, "read": 1, "write": 2, "admin": 3}[access_level]

    @staticmethod
    def from_int(access_level):
        return ["none", "read", "write", "admin"][value]

    @staticmethod
    def check_access(test_level, min_level):
        """Check access level against minimum required level"""
        return to_int(test_level) >= to_int(min_level)


