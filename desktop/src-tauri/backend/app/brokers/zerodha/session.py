_access_token = None

def save_token(token: str):
    global _access_token
    _access_token = token

def is_logged_in():
    return _access_token is not None
