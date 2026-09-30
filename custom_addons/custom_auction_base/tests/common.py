# -*- coding: utf-8 -*-
"""Test base classes — TSD-AUC-001 #18.1.1.

TransactionCase runs inside a transaction that is rolled back at teardown and
never commits. Two consequences make it structurally unable to cover parts of
this specification, and BOTH produce tests that pass while asserting nothing,
which is the most damaging failure a suite can have:

  1. Concurrency. Worker threads opening their own cursors cannot see
     uncommitted fixture data, so every thread operates on an empty database.
     A concurrency test written on TransactionCase passes trivially and
     exercises no lock at all.

  2. Post-commit hooks. cr.postcommit callbacks fire only on a real commit.
     Every assertion about bus publication and notification enqueue is
     vacuous under TransactionCase.

These two classes are the FIRST thing built in stage 0, before any mechanism
work. They are slow and fiddly, and the predictable organisational failure is
that someone marks the concurrency suite skipped to unblock a release, after
which the property it protects regresses unobserved until a live event.
A skipped concurrency test is a release blocker in its own right.
"""
import logging
import threading

from odoo import api, registry, SUPERUSER_ID
from odoo.tests import common

_logger = logging.getLogger(__name__)


class AuctionCommittedCase(common.TransactionCase):
    """Fixtures are COMMITTED so other cursors can see them.

    Teardown is guarded: a failure mid-test must not leave rows behind in the
    test database.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._committed_ids = []

    def _commit(self):
        self.env.flush_all()
        self.env.cr.commit()          # deliberate: see class docstring

    def _track(self, records):
        self._committed_ids.append((records._name, records.ids))
        return records

    def tearDown(self):
        try:
            for model, ids in reversed(self._committed_ids):
                try:
                    with registry(self.env.cr.dbname).cursor() as cr:
                        env = api.Environment(cr, SUPERUSER_ID, {})
                        env.cr.execute(
                            "DELETE FROM %s WHERE id IN %%s"
                            % env[model]._table, (tuple(ids),))
                except Exception:
                    _logger.exception("fixture cleanup failed for %s", model)
            self._committed_ids = []
        finally:
            super().tearDown()


class AuctionConcurrencyCase(AuctionCommittedCase):
    """Genuine independent cursors in worker threads.

    Tests that simulate concurrency within a single cursor are prohibited and
    rejected at review: they cannot exercise the lock path.
    """

    def run_concurrently(self, fn, count, timeout=60):
        """Run ``fn(index, env)`` in ``count`` threads, each on its own cursor.

        A threading.Barrier makes every thread contend at the same instant.
        Never use sleep for coordination -- it makes the test non-deterministic
        and the failure it is meant to catch intermittent.
        """
        barrier = threading.Barrier(count)
        results, errors = [None] * count, [None] * count
        dbname = self.env.cr.dbname
        uid = self.env.uid

        def worker(index):
            try:
                with registry(dbname).cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    barrier.wait(timeout=timeout)
                    results[index] = fn(index, env)
            except Exception as exc:          # noqa: BLE001 - recorded, re-raised by caller
                errors[index] = exc

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=timeout)
        return results, errors

    def assert_fixtures_visible(self, model, domain):
        """TST-CNC-016 sanity check.

        Guards against a vacuously passing suite: if fixtures were not
        committed, an independent cursor sees nothing and every concurrency
        assertion below becomes meaningless.
        """
        with registry(self.env.cr.dbname).cursor() as cr:
            env = api.Environment(cr, self.env.uid, {})
            found = env[model].search_count(domain)
        self.assertGreater(
            found, 0,
            "Fixtures are not visible to an independent cursor. The "
            "concurrency suite would pass while testing nothing.")
