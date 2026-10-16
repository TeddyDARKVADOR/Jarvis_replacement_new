"""Module de test d'incidents.selftest : une exception levée dans un fil, depuis le dépôt."""


def boom() -> None:
    raise ValueError("échec simulé numéro 42")
