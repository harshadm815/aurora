"""Phase 1: tensor mathematics, each backward pass checked numerically."""

import math

import numpy as np
import pytest

from aurora_core.tensors import (
    check_gradient,
    cross_entropy,
    cross_entropy_backward,
    gelu,
    gelu_backward,
    layer_norm,
    layer_norm_backward,
    linear,
    linear_backward,
    log_softmax,
    log_softmax_backward,
    numerical_gradient,
    relative_error,
    softmax,
    softmax_backward,
)



@pytest.fixture
def rng():
    return np.random.default_rng(1234)


def weighted_sum(output_fn, weights):
    """Turn a tensor-valued function into a scalar loss: sum(output * weights).
    Its gradient with respect to the output is exactly `weights`."""
    return lambda: float(np.sum(output_fn() * weights))


# ------------------------------------------------------------ gradcheck itself


def test_numerical_gradient_of_known_function():
    x = np.array([1.0, -2.0, 3.0])
    grad = numerical_gradient(lambda: float(np.sum(x**2)), x)
    np.testing.assert_allclose(grad, 2 * x, rtol=1e-8)


def test_numerical_gradient_restores_input():
    x = np.array([[0.5, 1.5], [2.5, -3.5]])
    before = x.copy()
    numerical_gradient(lambda: float(np.sum(np.sin(x))), x)
    np.testing.assert_array_equal(x, before)


def test_numerical_gradient_restores_input_when_f_fails():
    x = np.array([1.0, 2.0])

    def broken():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        numerical_gradient(broken, x)
    np.testing.assert_array_equal(x, [1.0, 2.0])


def test_numerical_gradient_requires_float64():
    with pytest.raises(ValueError):
        numerical_gradient(lambda: 0.0, np.zeros(3, dtype=np.float32))


def test_gradient_check_catches_a_wrong_gradient():
    x = np.array([1.0, 2.0, 3.0])
    result = check_gradient(lambda: float(np.sum(x**2)), x, analytic=3 * x)
    assert not result.passed
    # Even a small error in one entry is caught.
    nearly = 2 * x
    nearly[1] *= 1.001
    assert not check_gradient(lambda: float(np.sum(x**2)), x, analytic=nearly).passed
    assert check_gradient(lambda: float(np.sum(x**2)), x, analytic=2 * x).passed


def test_gradient_check_rejects_wrong_shape():
    x = np.zeros(3)
    with pytest.raises(ValueError):
        check_gradient(lambda: 0.0, x, analytic=np.zeros(4))


def test_relative_error():
    assert relative_error(np.array([1.0]), np.array([1.0])) == 0.0
    assert relative_error(np.array([0.0]), np.array([0.0])) == 0.0
    assert relative_error(np.array([1.0]), np.array([3.0])) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        relative_error(np.zeros(2), np.zeros(3))


# ------------------------------------------------------------------- linear


def test_linear_forward(rng):
    x = rng.normal(size=(2, 3, 4))
    w = rng.normal(size=(4, 5))
    b = rng.normal(size=5)
    y = linear(x, w, b)
    assert y.shape == (2, 3, 5)
    np.testing.assert_allclose(y[1, 2], x[1, 2] @ w + b)


def test_linear_rejects_bad_shapes(rng):
    with pytest.raises(ValueError):
        linear(rng.normal(size=(2, 3)), rng.normal(size=(4, 5)))
    with pytest.raises(ValueError):
        linear(rng.normal(size=(2, 4)), rng.normal(size=(4, 5)), rng.normal(size=4))
    with pytest.raises(ValueError):
        linear(rng.normal(size=(2, 4)), rng.normal(size=4))


def test_linear_gradients(rng):
    x = rng.normal(size=(2, 3, 4))
    w = rng.normal(size=(4, 5))
    b = rng.normal(size=5)
    upstream = rng.normal(size=(2, 3, 5))
    dx, dw, db = linear_backward(upstream, x, w)
    loss = weighted_sum(lambda: linear(x, w, b), upstream)
    assert check_gradient(loss, x, dx).passed
    assert check_gradient(loss, w, dw).passed
    assert check_gradient(loss, b, db).passed


# ------------------------------------------------------------------ softmax


def test_softmax_rows_sum_to_one(rng):
    y = softmax(rng.normal(size=(3, 7)))
    np.testing.assert_allclose(y.sum(axis=-1), 1.0)
    assert np.all(y > 0)


def test_softmax_is_stable_for_large_values():
    y = softmax(np.array([1000.0, 1001.0, 1002.0]))
    assert np.all(np.isfinite(y))
    np.testing.assert_allclose(y, softmax(np.array([0.0, 1.0, 2.0])))


def test_softmax_gives_masked_entries_zero():
    y = softmax(np.array([0.0, -np.inf, 1.0]))
    assert y[1] == 0.0
    assert y.sum() == pytest.approx(1.0)


def test_softmax_other_axis(rng):
    x = rng.normal(size=(4, 3))
    np.testing.assert_allclose(softmax(x, axis=0), softmax(x.T).T)


def test_softmax_gradient(rng):
    x = rng.normal(size=(2, 3, 6))
    upstream = rng.normal(size=x.shape)
    analytic = softmax_backward(upstream, softmax(x))
    assert check_gradient(weighted_sum(lambda: softmax(x), upstream), x, analytic).passed


def test_log_softmax_matches_log_of_softmax(rng):
    x = rng.normal(size=(3, 5))
    np.testing.assert_allclose(log_softmax(x), np.log(softmax(x)))


def test_log_softmax_is_finite_for_extreme_values():
    out = log_softmax(np.array([-1000.0, 0.0, 1000.0]))
    assert np.all(np.isfinite(out))


def test_log_softmax_gradient(rng):
    x = rng.normal(size=(4, 6))
    upstream = rng.normal(size=x.shape)
    analytic = log_softmax_backward(upstream, x)
    assert check_gradient(weighted_sum(lambda: log_softmax(x), upstream), x, analytic).passed


# ------------------------------------------------------------ cross-entropy


def test_cross_entropy_matches_definition(rng):
    logits = rng.normal(size=(3, 5))
    targets = np.array([0, 4, 2])
    p = softmax(logits)
    expected = -np.mean(np.log(p[np.arange(3), targets]))
    assert cross_entropy(logits, targets) == pytest.approx(expected)


def test_cross_entropy_of_uniform_logits_is_log_vocab():
    logits = np.zeros((4, 10))
    assert cross_entropy(logits, np.array([1, 2, 3, 9])) == pytest.approx(math.log(10))


def test_cross_entropy_ignore_index(rng):
    logits = rng.normal(size=(4, 5))
    targets = np.array([1, -100, 3, -100])
    full = cross_entropy(logits[[0, 2]], targets[[0, 2]])
    assert cross_entropy(logits, targets, ignore_index=-100) == pytest.approx(full)
    grad = cross_entropy_backward(logits, targets, ignore_index=-100)
    assert np.all(grad[[1, 3]] == 0.0)


def test_cross_entropy_rejects_bad_targets(rng):
    logits = rng.normal(size=(2, 5))
    with pytest.raises(ValueError):
        cross_entropy(logits, np.array([0, 5]))       # out of range
    with pytest.raises(ValueError):
        cross_entropy(logits, np.array([0.0, 1.0]))   # not integers
    with pytest.raises(ValueError):
        cross_entropy(logits, np.array([0, 1, 2]))    # wrong shape
    with pytest.raises(ValueError):
        cross_entropy(logits, np.array([-1, -1]), ignore_index=-1)  # nothing left


def test_cross_entropy_gradient(rng):
    logits = rng.normal(size=(2, 3, 7))
    targets = rng.integers(0, 7, size=(2, 3))
    targets[0, 1] = -1
    analytic = cross_entropy_backward(logits, targets, ignore_index=-1)
    loss = lambda: cross_entropy(logits, targets, ignore_index=-1)  # noqa: E731
    assert check_gradient(loss, logits, analytic).passed


# --------------------------------------------------------------------- GELU


def test_gelu_known_values():
    np.testing.assert_allclose(gelu(np.array([0.0])), [0.0])
    # For large |x| GELU approaches ReLU.
    np.testing.assert_allclose(gelu(np.array([10.0, -10.0])), [10.0, 0.0], atol=1e-6)
    # Tanh approximation at x = 1.
    assert gelu(np.array([1.0]))[0] == pytest.approx(0.8411920, abs=1e-6)


def test_gelu_gradient(rng):
    x = rng.normal(scale=2.0, size=(3, 8))
    upstream = rng.normal(size=x.shape)
    analytic = gelu_backward(upstream, x)
    assert check_gradient(weighted_sum(lambda: gelu(x), upstream), x, analytic).passed


# --------------------------------------------------------------- layer norm


def test_layer_norm_normalises(rng):
    x = rng.normal(loc=5.0, scale=3.0, size=(4, 16))
    y = layer_norm(x, np.ones(16), np.zeros(16))
    np.testing.assert_allclose(y.mean(axis=-1), 0.0, atol=1e-10)
    np.testing.assert_allclose(y.var(axis=-1), 1.0, atol=1e-4)


def test_layer_norm_applies_gamma_and_beta(rng):
    x = rng.normal(size=(2, 4))
    gamma = np.array([1.0, 2.0, 3.0, 4.0])
    beta = np.array([0.5, 0.5, -0.5, -0.5])
    plain = layer_norm(x, np.ones(4), np.zeros(4))
    np.testing.assert_allclose(layer_norm(x, gamma, beta), plain * gamma + beta)


def test_layer_norm_of_constant_vector_is_finite():
    y = layer_norm(np.full((1, 8), 3.0), np.ones(8), np.zeros(8))
    assert np.all(np.isfinite(y))
    np.testing.assert_allclose(y, 0.0)


def test_layer_norm_rejects_bad_parameters():
    with pytest.raises(ValueError):
        layer_norm(np.zeros((2, 4)), np.ones(3), np.zeros(4))


def test_layer_norm_gradients(rng):
    x = rng.normal(size=(2, 3, 6))
    gamma = rng.normal(size=6)
    beta = rng.normal(size=6)
    upstream = rng.normal(size=x.shape)
    dx, dgamma, dbeta = layer_norm_backward(upstream, x, gamma)
    loss = weighted_sum(lambda: layer_norm(x, gamma, beta), upstream)
    assert check_gradient(loss, x, dx).passed
    assert check_gradient(loss, gamma, dgamma).passed
    assert check_gradient(loss, beta, dbeta).passed
