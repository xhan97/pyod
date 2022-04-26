# -*- coding: utf-8 -*-
"""Isolation-based anomaly detection using nearest-neighbor ensembles.
Part of the codes are adapted from https://github.com/xhan97/inne
"""
# Author: Xin Han <xinhan197@gmail.com>
# License: BSD 2 clause

from __future__ import division
from __future__ import print_function

import numbers
from warnings import warn

import numpy as np
from sklearn.utils import check_array
from sklearn.utils.validation import check_is_fitted
from sklearn.metrics import euclidean_distances

from .base import BaseDetector
from ..utils.utility import invert_order


class INNE(BaseDetector):
    """ Isolation-based anomaly detection using nearest-neighbor ensembles.

    The INNE algorithm uses the nearest neighbour ensemble to isolate anomalies.
    It partitions the data space into regions using a subsample and determines an
    isolation score for each region. As each region adapts to local distribution,
    the calculated isolation score is a local measure that is relative to the local
    neighbourhood, enabling it to detect both global and local anomalies. INNE has 
    linear time complexity to efficiently handle large and high-dimensional datasets
    with complex distributions.
    See :cite:`bandaragoda2018isolation` for details.

    Parameters
    ----------
    n_estimators : int, default=200
        The number of base estimators in the ensemble.

    max_samples : int or float, optional (default="auto")
        The number of samples to draw from X to train each base estimator.

            - If int, then draw `max_samples` samples.
            - If float, then draw `max_samples` * X.shape[0]` samples.
            - If "auto", then `max_samples=min(8, n_samples)`.

    contamination : float in (0., 0.5), optional (default=0.1)
        The amount of contamination of the data set, i.e. the proportion
        of outliers in the data set. Used when fitting to define the threshold
        on the decision function.

    random_state : int, RandomState instance or None, default=None
        Controls the pseudo-randomness of the selection of the feature
        and split values for each branching step and each tree in the forest.

        Pass an int for reproducible results across multiple function calls.
        See :term:`Glossary <random_state>`.

    Attributes
    ----------
    max_samples_ : integer
        The actual number of samples

    decision_scores_ : numpy array of shape (n_samples,)
        The outlier scores of the training data.
        The higher, the more abnormal. Outliers tend to have higher
        scores. This value is available once the detector is
        fitted.

    threshold_ : float
        The threshold is based on ``contamination``. It is the
        ``n_samples * contamination`` most abnormal samples in
        ``decision_scores_``. The threshold is calculated for generating
        binary outlier labels.

    labels_ : int, either 0 or 1
        The binary labels of the training data. 0 stands for inliers
        and 1 for outliers/anomalies. It is generated by applying
        ``threshold_`` on ``decision_scores_``.
    """

    def __init__(self,
                 n_estimators=200,
                 max_samples="auto",
                 contamination=0.1,
                 random_state=None):
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.random_state = random_state
        self.contamination = contamination

    def fit(self, X, y=None):
        """Fit detector. y is ignored in unsupervised methods.

        Parameters
        ----------
        X : numpy array of shape (n_samples, n_features)
            The input samples.

        y : Ignored
            Not used, present for API consistency by convention.

        Returns
        -------
        self : object
            Fitted estimator.
        """
        # validate inputs X and y (optional)

        # Check data
        X = check_array(X, accept_sparse=False)
        self._set_n_classes(y)

        n_samples = X.shape[0]
        if isinstance(self.max_samples, str):
            if self.max_samples == "auto":
                max_samples = min(16, n_samples)
            else:
                raise ValueError(
                    "max_samples (%s) is not supported."
                    'Valid choices are: "auto", int or'
                    "float"
                    % self.max_samples
                )

        elif isinstance(self.max_samples, numbers.Integral):
            if self.max_samples > n_samples:
                warn(
                    "max_samples (%s) is greater than the "
                    "total number of samples (%s). max_samples "
                    "will be set to n_samples for estimation."
                    % (self.max_samples, n_samples)
                )
                max_samples = n_samples
            else:
                max_samples = self.max_samples
        else:  # float
            if not 0.0 < self.max_samples <= 1.0:
                raise ValueError(
                    "max_samples must be in (0, 1], got %r" % self.max_samples
                )
            max_samples = int(self.max_samples * X.shape[0])
        self.max_samples_ = max_samples

        self._fit(X)
        self.decision_scores_ = invert_order(self._score_samples(X))
        self._process_decision_scores()
        return self

    def _fit(self, X):
        """ Build n_estimators sets of hyperspheres. 

        Parameters
        ----------
        X : numpy array of shape (n_samples, n_features)
            The training input samples. 

        Returns
        -------
        self : object
        """

        n, m = X.shape
        self._centroids = np.empty(
            [self.n_estimators, self.max_samples_, m])
        self._ratio = np.empty([self.n_estimators, self.max_samples_])
        self._centroids_radius = np.empty(
            [self.n_estimators, self.max_samples_])

        for i in range(self.n_estimators):
            if isinstance(self.random_state, numbers.Integral):
                if i == 0:
                    rn_seed = self.random_state
                else:
                    rn_seed += 5
                np.random.seed(rn_seed)
            # randomly selected subsamples of size max_samples_ as centroids.
            center_index = np.random.choice(
                n, self.max_samples_, replace=False)
            self._centroids[i] = X[center_index]
            center_dist = euclidean_distances(
                self._centroids[i], self._centroids[i], squared=True)
            np.fill_diagonal(center_dist, np.inf)
            # radius of each hypersphere is the Nearest Neighbors distance of centroid.
            self._centroids_radius[i] = np.amin(center_dist, axis=1)
            # Nearest Neighbors of centroids
            cnn_index = np.argmin(center_dist, axis=1)
            cnn_radius = self._centroids_radius[i][cnn_index]

            self._ratio[i] = 1 - cnn_radius / self._centroids_radius[i]
        return self

    def decision_function(self, X):
        """Predict raw anomaly score of X using the fitted detector.

        The anomaly score of an input sample is computed based on different
        detector algorithms. For consistency, outliers are assigned with
        larger anomaly scores.

        Parameters
        ----------
        X : numpy array of shape (n_samples, n_features)
            The training input samples. 

        Returns
        -------
        anomaly_scores : numpy array of shape (n_samples,)
            The anomaly score of the input samples.
        """
        check_is_fitted(self, ['decision_scores_', 'threshold_', 'labels_'])
        # invert outlier scores. Outliers comes with higher outlier scores
        return invert_order(self._score_samples(X))

    def _score_samples(self, X):
        """
        Opposite of the anomaly score defined in the original paper.
        The anomaly score of an input sample is computed as
        the mean anomaly score over all set of hyperspheres.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input samples.

        Returns
        -------
        scores : ndarray of shape (n_samples,)
            The anomaly score of the input samples.
            The lower, the more abnormal.
        """

        # check data
        X = check_array(X, accept_sparse=False)
        isolation_scores = np.ones([self.n_estimators, X.shape[0]])

        # each test instance is evaluated against n_estimators sets of hyperspheres
        for i in range(self.n_estimators):
            x_dists = euclidean_distances(X, self._centroids[i],  squared=True)
            # find instances that are covered by at least one hypersphere.
            cover_radius = np.where(
                x_dists <= self._centroids_radius[i], self._centroids_radius[i], np.nan)
            x_covered = np.where(~np.isnan(cover_radius).all(axis=1))
            # the centroid of the hypersphere covering x and having the smallest radius
            cnn_x = np.nanargmin(cover_radius[x_covered], axis=1)
            isolation_scores[i][x_covered] = self._ratio[i][cnn_x]
        # the isolation scores are averaged to produce the anomaly score
        scores = np.mean(isolation_scores, axis=0)
        return -scores
