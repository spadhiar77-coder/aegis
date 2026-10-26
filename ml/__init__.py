"""Offline machine-learning code: labelled data generation, training, evaluation.

Nothing in here is imported by the running API except through the files it
produces (app/model/model.json). `ml.simulation` is standard-library only so
the API simulator script can use it without extra installs; the training code
needs NumPy and scikit-learn (requirements-ml.txt).
"""
