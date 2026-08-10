

class CommandNotFoundException(Exception):
    def __init__(self, message : str = "Requested command not found"):
        super().__init__(message)

class NetworkTypeNotFoundException(Exception):
    def __init__(self, message : str = "Network type not recognized."):
        super().__init__(message)
        
class CommandExecutionError(Exception):
    def __init__(self, message : str = "Error executing command."):
        super().__init__(message)

class PasswordHashError(Exception):
    """Raised when a password cannot be hashed in the requested format.

    Hashing is not something to fall back on quietly: producing sha512crypt
    where yescrypt was asked for would write a config that disagrees with what
    Arch's own ``passwd`` stores (and with what ``sync`` captures back).
    """
    def __init__(self, message: str = "Could not hash the password."):
        super().__init__(message)


class ConfigValidationError(Exception):
    """Raised when a value from the user's config is unsafe or malformed.

    Used to reject untrusted config values before they reach a command line
    (e.g. a package name with shell metacharacters that would be interpolated
    into an AUR build's ``su -c`` string, or a name starting with ``-`` that
    pacman would parse as a flag).
    """
    def __init__(self, message : str = "Invalid configuration value."):
        super().__init__(message)