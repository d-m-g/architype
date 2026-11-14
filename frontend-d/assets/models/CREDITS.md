# 3D model credits

Every model here is from one kit, so the fixtures share a style.

**Furniture Kit (2.0)** by Kenney (www.kenney.nl), https://kenney.nl/assets/furniture-kit
Licence: Creative Commons Zero (CC0 1.0), http://creativecommons.org/publicdomain/zero/1.0/
Credit is not required by the licence; it is given here anyway.

| File | Kit model (`Models/GLTF format/`) | Used for |
|---|---|---|
| `toilet.glb` | `toilet.glb` | toilet |
| `bathroomSinkSquare.glb` | `bathroomSinkSquare.glb` | washbasin (a sink outside a kitchen) |
| `kitchenSink.glb` | `kitchenSink.glb` | kitchen counter with sink (a sink in a kitchen) |
| `bathtub.glb` | `bathtub.glb` | bathtub |
| `shower.glb` | `shower.glb` | shower |

The files are byte-for-byte copies from the kit's zip. `room-renderer.js` swaps their unlit materials for lit ones at load,
keeping the colours, so they take the scene's sun and shadows.

The loader, `vendor/three/examples/jsm/loaders/GLTFLoader.js` with `utils/BufferGeometryUtils.js` and `utils/SkeletonUtils.js`,
is from the same three.js 0.185.1 package as the rest of `vendor/three` (MIT, see `vendor/three/LICENSE`).
