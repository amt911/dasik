from typing import List


def su_argv(user: str, script: str, *args: str) -> List[str]:
    """``su - <user> -c <script> -- sh <args>``.

    ``--`` terminates util-linux ``su``'s own option parsing before the
    shell's positional argv, and every value travels as ``$1``.. so a name
    with shell metacharacters arrives as inert data instead of code.
    """
    return ["-", user, "-c", script, "--", "sh", *args]
