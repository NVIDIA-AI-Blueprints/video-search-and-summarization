# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""NumPy-only replacements for the two OpenCV routines used by this container.

Rationale
---------
``opencv-python-headless`` was pulled in solely for two pure linear-algebra
helpers.  Its wheel bundles 16 native shared libraries (FFmpeg family under
LGPL, OpenSSL 1.1.1w which is end-of-life, and others) that are irrelevant to
this workload but must still be license- and CVE-reviewed.  This module
reimplements the exact numerical behaviour of those two routines using only
``numpy``, so that the OpenCV dependency can be dropped entirely.

Provided functions
------------------
``perspective_transform_3d_to_2d``  replaces ``cv2.perspectiveTransform``
``decompose_projection_matrix``     replaces ``cv2.decomposeProjectionMatrix``
``rq_decomp3x3``                    replaces ``cv2.RQDecomp3x3`` (helper)

Every function follows OpenCV's conventions bit-for-bit where OpenCV's
behaviour is deterministic; the two places where OpenCV is itself ambiguous
(overall sign of the SVD null vector, and RQ decomposition sign choice) are
documented explicitly on the functions concerned.
"""

import math

import numpy as np

# Constants used by OpenCV's C++ implementations, replicated here so that the
# branch points of the numpy version land in exactly the same places.
_FLT_EPSILON = 1.1920928955078125e-07  # FLT_EPSILON
_DBL_EPSILON = 2.220446049250313e-16  # DBL_EPSILON

__all__ = [
    "perspective_transform_3d_to_2d",
    "rq_decomp3x3",
    "decompose_projection_matrix",
]


def perspective_transform_3d_to_2d(src, m):
    """Replacement for ``cv2.perspectiveTransform(src, m)`` for 3D -> 2D points.

    Applies the 3x4 matrix ``m`` to each 3D point in homogeneous coordinates and
    divides by the resulting last component (perspective divide).

    Parameters
    ----------
    src : array_like
        Point array whose *last* axis has length 3.  Any leading shape is
        allowed; OpenCV treats a ``(H, W, 3)`` numpy array as an ``H x W``
        3-channel Mat, and an ``(N, 1, 3)`` array as an ``N x 1`` 3-channel Mat.
        Both work here, as does a plain ``(N, 3)``.
    m : array_like
        ``3 x 4`` transform matrix (``dst_channels + 1`` by ``src_channels + 1``).

    Returns
    -------
    numpy.ndarray
        Same leading shape as ``src`` with a trailing axis of length 2, and the
        same dtype as ``src`` (float32 in, float32 out; anything else is
        promoted to float64, matching OpenCV which only accepts CV_32F/CV_64F).

    OpenCV behaviours replicated exactly
    ------------------------------------
    * Arithmetic is performed in ``float64`` regardless of the input dtype, and
      the result is cast back to the input dtype only at the very end.  OpenCV's
      ``perspectiveTransform_`` templates do the same.
    * The perspective divide is implemented as a reciprocal followed by a
      multiply (``w = 1./w; out = f * w``), *not* as a direct division.  These
      differ in the last ulp, so the reciprocal form is used here to match.
    * **Degenerate points**: when ``|w| <= FLT_EPSILON`` OpenCV does *not*
      produce ``inf``/``nan``; it writes ``0`` for every output component of
      that point.  That guard is reproduced here.  This matters for points at
      or near the camera's principal plane (depth ~ 0).

    Notes
    -----
    The corresponding OpenCV code is the ``scn == 3 && dcn == 2`` branch of
    ``perspectiveTransform_`` in ``modules/core/src/matmul.dispatch.cpp``.
    """
    src_arr = np.asarray(src)
    if src_arr.shape[-1] != 3:
        raise ValueError(
            f"src last axis must have length 3, got shape {src_arr.shape}"
        )

    # OpenCV only accepts CV_32F and CV_64F; preserve float32 round-tripping and
    # promote everything else (including integers) to float64.
    out_dtype = np.float32 if src_arr.dtype == np.float32 else np.float64

    pts = src_arr.astype(np.float64, copy=False)
    mat = np.asarray(m, dtype=np.float64)
    if mat.shape != (3, 4):
        raise ValueError(f"m must have shape (3, 4), got {mat.shape}")

    flat = pts.reshape(-1, 3)
    x, y, z = flat[:, 0], flat[:, 1], flat[:, 2]

    w = x * mat[2, 0] + y * mat[2, 1] + z * mat[2, 2] + mat[2, 3]
    u = x * mat[0, 0] + y * mat[0, 1] + z * mat[0, 2] + mat[0, 3]
    v = x * mat[1, 0] + y * mat[1, 1] + z * mat[1, 2] + mat[1, 3]

    good = np.abs(w) > _FLT_EPSILON

    out = np.zeros((flat.shape[0], 2), dtype=np.float64)
    # Reciprocal-then-multiply, exactly as OpenCV does it.
    inv_w = np.reciprocal(np.where(good, w, 1.0))
    out[:, 0] = np.where(good, u * inv_w, 0.0)
    out[:, 1] = np.where(good, v * inv_w, 0.0)

    return out.reshape(src_arr.shape[:-1] + (2,)).astype(out_dtype, copy=False)


def rq_decomp3x3(matrix):
    """Replacement for ``cv2.RQDecomp3x3`` restricted to the outputs we need.

    Factors a 3x3 matrix ``M`` into ``M = R @ Q`` where ``R`` is upper
    triangular and ``Q`` is orthonormal.

    This is a *literal* line-by-line translation of ``cv::RQDecomp3x3`` in
    ``modules/calib3d/src/calibration_base.cpp`` (OpenCV 4.x), so that even
    OpenCV's idiosyncrasies are reproduced rather than "fixed".

    Returns
    -------
    (R, Q) : tuple of numpy.ndarray
        ``R`` upper triangular (the camera matrix, up to scale),
        ``Q`` orthonormal.

    OpenCV conventions replicated
    -----------------------------
    * Three Givens rotations in the order X, Y, Z, giving
      ``R = M @ Qx @ Qy @ Qz`` and ``Q = Qz.T @ Qy.T @ Qx.T``.
    * **Degeneracy guard.**  OpenCV decides whether to rotate at all by looking
      *only at the entry being eliminated*::

          s = |M[2,1]| > DBL_EPSILON ? M[2,1] : 0.0
          c = |M[2,1]| > DBL_EPSILON ? M[2,2] : 1.0
          z = 1 / sqrt(c*c + s*s)

      i.e. when the entry to eliminate is already negligible it substitutes the
      identity rotation (c=1, s=0) rather than normalising a near-zero pair.
      Note this is *not* the same as adding an epsilon inside the square root;
      getting this wrong silently destroys the decomposition whenever
      ``M[2,2]`` is tiny while ``M[2,1]`` is exactly zero.
    * Sub-diagonal entries are forced to exactly zero after each rotation.
    * **Sign disambiguation**: OpenCV wants ``R[0,0] >= 0`` and ``R[1,1] >= 0``
      (the leading two diagonal entries of the camera matrix).  ``R[2,2]`` is
      deliberately *not* forced positive -- it carries the overall scale of the
      projection matrix and is frequently negative in practice.  The three
      branches negate specific entries of ``R`` and of ``Qx``/``Qy``/``Qz``;
      those mutations are transcribed verbatim below, including the
      ``Qz = Qz.T`` / ``Qy = Qy.T`` steps in the second and third branches.

    Raises
    ------
    ValueError
        Mirroring OpenCV's two active ``CV_Assert`` checks, which fire when the
        residual of an eliminated entry exceeds ``FLT_EPSILON`` in *absolute*
        terms.  Because the threshold is absolute rather than relative, badly
        scaled matrices (entries around 1e8 and above) can trip it purely
        through round-off.  OpenCV raises ``cv2.error`` in exactly these cases.

    Not reproduced
    --------------
    ``Qx``, ``Qy``, ``Qz`` and the Euler angles.  Nothing in this container
    consumes them, and OpenCV's Euler-angle sign conventions are a separate
    and considerably more error-prone thing to match.
    """
    mat = np.array(matrix, dtype=np.float64)
    if mat.shape != (3, 3):
        raise ValueError(f"matrix must have shape (3, 3), got {mat.shape}")

    # --- Givens rotation about x, eliminating M[2, 1] ---
    pivot = abs(mat[2, 1]) > _DBL_EPSILON
    s = mat[2, 1] if pivot else 0.0
    c = mat[2, 2] if pivot else 1.0
    z = 1.0 / math.sqrt(c * c + s * s)
    c *= z
    s *= z
    qx = np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]])
    r = mat @ qx
    # OpenCV has a plain assert() here, which is compiled out of release
    # builds (NDEBUG), so it is deliberately not enforced.
    r[2, 1] = 0.0

    # --- Givens rotation about y, eliminating M[2, 0] ---
    pivot = abs(r[2, 0]) > _DBL_EPSILON
    s = -r[2, 0] if pivot else 0.0
    c = r[2, 2] if pivot else 1.0
    z = 1.0 / math.sqrt(c * c + s * s)
    c *= z
    s *= z
    qy = np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])
    m = r @ qy
    if not abs(m[2, 0]) < _FLT_EPSILON:  # OpenCV: CV_Assert (always active)
        raise ValueError(
            "RQ decomposition failed to eliminate M[2, 0] "
            f"(residual {m[2, 0]!r} >= FLT_EPSILON). OpenCV raises cv2.error "
            "here too; the input matrix is too badly scaled or too "
            "ill-conditioned for this decomposition."
        )
    m[2, 0] = 0.0

    # --- Givens rotation about z, eliminating M[1, 0] ---
    pivot = abs(m[1, 0]) > _DBL_EPSILON
    s = m[1, 0] if pivot else 0.0
    c = m[1, 1] if pivot else 1.0
    z = 1.0 / math.sqrt(c * c + s * s)
    c *= z
    s *= z
    qz = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    r = m @ qz
    if not abs(r[1, 0]) < _FLT_EPSILON:  # OpenCV: CV_Assert (always active)
        raise ValueError(
            "RQ decomposition failed to eliminate M[1, 0] "
            f"(residual {r[1, 0]!r} >= FLT_EPSILON). OpenCV raises cv2.error "
            "here too; the input matrix is too badly scaled or too "
            "ill-conditioned for this decomposition."
        )
    r[1, 0] = 0.0

    # --- Resolve the decomposition ambiguity (positive leading diagonal) ---
    # Transcribed verbatim from OpenCV, including the transposes.
    if r[0, 0] < 0:
        if r[1, 1] < 0:
            # rotate 180 degrees about z
            r[0, 0] *= -1
            r[0, 1] *= -1
            r[1, 1] *= -1

            qz[0, 0] *= -1
            qz[0, 1] *= -1
            qz[1, 0] *= -1
            qz[1, 1] *= -1
        else:
            # rotate 180 degrees about y
            r[0, 0] *= -1
            r[0, 2] *= -1
            r[1, 2] *= -1
            r[2, 2] *= -1

            qz = qz.T.copy()

            qy[0, 0] *= -1
            qy[0, 2] *= -1
            qy[2, 0] *= -1
            qy[2, 2] *= -1
    elif r[1, 1] < 0:
        # rotate 180 degrees about x
        r[0, 1] *= -1
        r[0, 2] *= -1
        r[1, 1] *= -1
        r[1, 2] *= -1
        r[2, 2] *= -1

        qz = qz.T.copy()
        qy = qy.T.copy()

        qx[1, 1] *= -1
        qx[1, 2] *= -1
        qx[2, 1] *= -1
        qx[2, 2] *= -1

    q = (qz.T @ qy.T) @ qx.T
    return r, q


def decompose_projection_matrix(proj_matrix):
    """Replacement for ``cv2.decomposeProjectionMatrix``, first three outputs.

    Decomposes a 3x4 projection matrix ``P`` into an intrinsic camera matrix,
    a rotation matrix, and the camera centre in homogeneous world coordinates,
    such that ``P ~ K @ [R | -R @ C]`` up to an overall scale.

    Parameters
    ----------
    proj_matrix : array_like
        ``3 x 4`` world-to-pixel projection matrix.

    Returns
    -------
    (camera_matrix, rot_matrix, trans_vect) : tuple of numpy.ndarray
        ``camera_matrix`` : ``(3, 3)`` float64, upper triangular.  **Not**
            normalised to ``K[2, 2] == 1``; it carries the scale of ``P``,
            exactly as OpenCV returns it.  Callers that want the conventional
            form must divide by ``K[2, 2]`` themselves (which is what
            ``generate_pub_sub_configs.py`` does).
        ``rot_matrix`` : ``(3, 3)`` float64 proper rotation, ``det == +1``.
        ``trans_vect`` : ``(4, 1)`` float64 **homogeneous** camera centre.  It
            is the null vector of ``P`` and is only defined up to scale *and
            sign*; divide by its last element to obtain inhomogeneous world
            coordinates.

    OpenCV behaviours replicated
    ----------------------------
    * ``trans_vect`` is obtained as the right singular vector belonging to the
      smallest singular value, i.e. the last row of ``V^T``.  OpenCV builds a
      4x4 matrix by appending a row of zeros to ``P`` and takes the SVD of
      that; the same padding is done here.  (The padding adds one zero singular
      value but does not change the right singular subspace, so it is
      mathematically redundant -- it is kept for fidelity.)
    * ``camera_matrix`` and ``rot_matrix`` come from an RQ decomposition of
      ``P[:, :3]`` with OpenCV's sign convention (see :func:`rq_decomp3x3`).

    Known divergence
    ----------------
    The **overall sign of ``trans_vect`` is not guaranteed to match OpenCV**.
    A null vector is only defined up to a non-zero scalar, and OpenCV's SVD and
    LAPACK's divide-and-conquer SVD may pick opposite signs.  Any legitimate
    consumer must therefore normalise by the last element (as
    ``generate_pub_sub_configs.py`` does via ``t[:2] / t[-1]``), which cancels
    the sign.  Callers that use the raw sign of ``trans_vect`` would *not* be
    safe against this substitution.

    Outputs 4-7 of ``cv2.decomposeProjectionMatrix`` (``rotMatrixX``,
    ``rotMatrixY``, ``rotMatrixZ``, ``eulerAngles``) are intentionally *not*
    reproduced: they are discarded at the only call site in this container.
    """
    p = np.asarray(proj_matrix, dtype=np.float64)
    if p.shape != (3, 4):
        raise ValueError(f"proj_matrix must have shape (3, 4), got {p.shape}")

    # Camera centre: the null vector of P (last row of V^T), matching OpenCV's
    # zero-padded 4x4 formulation.
    padded = np.vstack([p, np.zeros((1, 4))])
    _, _, vt = np.linalg.svd(padded)
    trans_vect = vt[3, :].reshape(4, 1).copy()

    camera_matrix, rot_matrix = rq_decomp3x3(p[:, :3])
    return camera_matrix, rot_matrix, trans_vect
