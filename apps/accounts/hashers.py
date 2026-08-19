from django.contrib.auth.hashers import Argon2PasswordHasher


class WorkLedgerArgon2PasswordHasher(Argon2PasswordHasher):
    """Argon2id tuned for a low-entropy PIN threat model."""

    time_cost = 4
    memory_cost = 102_400
    parallelism = 2
