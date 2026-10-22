# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Bring existing spreadsheets onto a single, linear revision chain.

    Stock spreadsheet_oca accepts revisions without checking their parent, so
    existing data may hold forked branches and stored SNAPSHOT rows. Browsers
    already discard those on load (they fail the parent check), so archiving
    them changes nothing users see; it only makes the chain the server now
    enforces match what clients replay. Archived rows stay recoverable.
    """
    for model_name in env["spreadsheet.abstract"]._collab_concrete_models():
        records = env[model_name].with_context(active_test=False).search([])
        for record in records:
            record._collab_rebuild_head()
        _logger.info("spreadsheet_oca_collab: rebuilt heads for %s (%s)",
                     model_name, len(records))
    env["spreadsheet.oca.revision"]._collab_create_unique_index()
