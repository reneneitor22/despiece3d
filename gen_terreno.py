# -*- coding: utf-8 -*-
"""Genera un terreno de prueba (malla ABIERTA, como sale de un topografico real)."""
import os, numpy as np, trimesh

nx, ny = 120, 90
X, Y = np.meshgrid(np.linspace(0, 120, nx), np.linspace(0, 90, ny))   # metros
Z = (9 * np.sin(X / 26.0) * np.cos(Y / 19.0)
     + 5 * np.sin(X / 11.0 + 1.3)
     + 14 * np.exp(-(((X - 82) ** 2) / 640.0 + ((Y - 30) ** 2) / 420.0))
     + 0.02 * X + 3.0)
Z -= Z.min()

V = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
F = []
for j in range(ny - 1):
    for i in range(nx - 1):
        a = j * nx + i; b = a + 1; c = a + nx; d = c + 1
        F += [[a, b, d], [a, d, c]]
m = trimesh.Trimesh(vertices=V, faces=np.array(F), process=False)
os.makedirs('out', exist_ok=True)
m.export('out/terreno_prueba.stl')
print('terreno: %.0f x %.0f m, desnivel %.1f m, %d caras, watertight=%s'
      % (X.max(), Y.max(), Z.max(), len(F), m.is_watertight))
