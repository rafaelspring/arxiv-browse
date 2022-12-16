"""Dissemination flask application"""
import os

from flask import Flask

from .routes import blueprint
from .trace import setup_trace

from cloudpathlib.anypath import to_anypath

import logging
logging.basicConfig(level=logging.INFO)

#################### config ####################
storage_prefix = os.environ.get('STORAGE_PREFIX','gs://arxiv-production-data')
"""Storage prefix to use. Ex gs://arxiv-production-data/ps_cache

Use something like /cache/ps_cache for a file system.

Use something like ./testing/data for testing data.

Should not end with a /.
"""

chunk_size = int(os.environ.get('CHUNK_SIZE', 1024 * 256 * 4))
"""chunk size from GS. Bytes. Must be mutiples of 256k"""

trace = bool(os.environ.get('TRACE', '1') == '1')
"""To activate Google logging and trace.

On by default, set to 0 to deactivate.
"""

#################### App ####################
app = Flask(__name__)

problems = []
if chunk_size % (1024 *256):
    problems.append('CHUNK_SIZE must be a multiple of 256kb.')
if storage_prefix.endswith('/'):
    problems.append(f'STORAGE_PREFIX should not end with a slash, prefix was {storage_prefix}')
if not to_anypath(storage_prefix).exists():
        problems.append('The {STORAGE_PREFIX} does not exist or cannot read.')
if problems:
    [app.logger.error(prob) for prob in problems]
    exit(1)

app.logger.info(f"trace is {trace}")
app.logger.info(f"storage_prefix is {storage_prefix}")

app.config.update(
    storage_prefix=storage_prefix,
    chunk_size=chunk_size,
    )

app.register_blueprint(blueprint)

############### trace and logging setup ###############
if trace:
    setup_trace(__name__,app)
