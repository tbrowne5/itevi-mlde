"""Populate the torch hub cache by invoking the esm loader.

Used by Dockerfile.naive. The multi-stage Dockerfile curls the checkpoints
directly instead, so the weights layer does not depend on the python env.
"""
import esm

esm.pretrained.esm2_t33_650M_UR50D()
print("weights cached")
