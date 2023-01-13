"""Functions related to articles.

These are focused on using the GS bucket abs and source files."""

import re
from typing import Union, Literal, Optional, Tuple, List

from dateutil import parser

from arxiv.identifier import Identifier


from arxiv_dissemination.services.object_store import FileObj, ObjectStore

from .key_patterns import abs_path_current_parent, abs_path_orig_parent, ps_cache_pdf_path, current_pdf_path, previous_pdf_path, abs_path_orig, abs_path_current, Formats

import logging
logger = logging.getLogger(__file__)
logger.setLevel(logging.DEBUG)


Conditions = Literal["WITHDRAWN", # Where the version is a WDR
                     "ARTICLE_NOT_FOUND", # Where there is no article
                     "VERSION_NOT_FOUND", # Where the article exists but the version does not
                     "NO_SOURCE", # Article and version exists but no source exists
                     "UNAVAIABLE", # Where the PDF unexpectedly does not exist
                     ]

AbsConditions = Literal["ARTICLE_NOT_FOUND",
                        "VERSION_NOT_FOUND",
                        "NO_ID"]

# From arxiv/browse/services/util/formats.py
VALID_SOURCE_EXTENSIONS = [
    '.tar.gz',
    '.pdf',
    '.ps.gz',
    '.gz',
    '.dvi.gz',
    '.html.gz',
]

src_regex = re.compile(r'.*(\.tar\.gz|\.pdf|\.ps\.gz|\.gz|\.div\.gz|\.html\.gz)')


RE_DATE_COMPONENTS = re.compile(
    r'^Date\s*(?::|\(revised\s*(?P<version>.*?)\):)\s*(?P<date>.*?)'
    r'(?:\s+\((?P<size_kilobytes>\d+)kb,?(?P<source_type>.*)\))?$')

v_regex = re.compile(r'.*v(\d+)')

def _path_to_version(path: FileObj):
    mtch = v_regex.search(path.name)
    if mtch:
        return int(mtch.group(1))
    else:
        return 0

class ArticleStore():
    def __init__(self, objstore: ObjectStore):
        self.objstore: ObjectStore = objstore

    def current_version(self, arxiv_id:Identifier) -> Optional[int]:
        """Gets the version number of the latest versoin of `arxiv_id`

        Returns None if there is no article witht this ID."""
        orgprefix =f"{abs_path_orig_parent(arxiv_id)}/{arxiv_id.filename}"
        abs_versions = list(self.objstore.list(orgprefix))
        if abs_versions:
            return max(map(_path_to_version, abs_versions)) + 1

        currprefix=abs_path_current(arxiv_id)
        if self.objstore.to_obj(currprefix).exists():
            return 1
        else:
            logger.debug(f"No current_version, since no objects found in {self.objstore} at {orgprefix} and {currprefix}")
            return None  # article does not exist

    def abs_for_id(self, arxiv_id: Identifier, version=0, current=0, any=False) -> Union[FileObj, AbsConditions]:
        first_version = (version != 0 and version == 1) or arxiv_id.version == 1
        if current or not arxiv_id.has_version or first_version:
            abs = self.objstore.to_obj(abs_path_current(arxiv_id))
            if abs.exists():
                return abs
            else:
                return "ARTICLE_NOT_FOUND" # should always be a current abs file

        version = version or arxiv_id.version
        abs = self.objstore.to_obj(abs_path_orig(arxiv_id, version=version))
        if abs.exists():
            return abs

        # All that is left is if a version is desired and that version is the one in ftp.
        # The version in ftp is one higher than the highest version in orig.
        abs = self.objstore.to_obj(abs_path_orig(arxiv_id, version=arxiv_id.version-1))
        if abs.exists():
            return abs
        else:
            return "VERSION_NOT_FOUND" # ambitious? what if the article doens't exist?


    def dissemination_for_id(self, format: Formats, arxiv_id: Identifier) -> Union[Conditions, FileObj]:
        """Gets FileObj for an `Identifier` with or without a version."""
        if format != "pdf":
            raise Exception("Only PDF is currently supported")

        if not arxiv_id.has_version:
            return self.dissemination_for_id_current(format, arxiv_id)

        ps_cache_pdf = self.objstore.to_obj(ps_cache_pdf_path(format, arxiv_id))
        if ps_cache_pdf.exists():
            return ps_cache_pdf

        non_current_pdf=self.objstore.to_obj(previous_pdf_path(arxiv_id))
        if non_current_pdf.exists():
            return non_current_pdf

        cur_version = self.current_version(arxiv_id)
        if not cur_version:
            return "ARTICLE_NOT_FOUND"
        if arxiv_id.version > cur_version:
            return "VERSION_NOT_FOUND"

        current_pdf = self.objstore.to_obj(current_pdf_path(arxiv_id))
        if current_pdf.exists():
            return current_pdf

        if self.is_withdrawn(arxiv_id):
            return "WITHDRAWN"
        if not self._source_exists(arxiv_id):
            return "WITHDRAWN"

        logger.debug("no file found for %s, tried %s", arxiv_id.idv,
                     [str(ps_cache_pdf), str(non_current_pdf), str(current_pdf)])
        return "UNAVAIABLE"

    def dissemination_for_id_current(self, format: Formats, arxiv_id: Identifier) -> Union[Conditions, FileObj]:
        """Gets PDF FileObj for most current version for `Identifier`."""
        version = self.current_version(arxiv_id)
        if not version:
            logger.debug("No current version found for article %s", arxiv_id.id)
            return "ARTICLE_NOT_FOUND"
        
        ps_cache_pdf = self.objstore.to_obj(ps_cache_pdf_path(format, arxiv_id, version))
        if ps_cache_pdf.exists():
            return ps_cache_pdf

        current_pdf = self.objstore.to_obj(current_pdf_path(arxiv_id))
        if current_pdf.exists():
            return current_pdf

        abs = self.abs_for_id(arxiv_id)
        if abs in ["ARTICLE_NOT_FOUND", "VERSION_NOT_FOUND"]:
            return abs

        if self.is_withdrawn(arxiv_id):
            return "WITHDRAWN"
        if not self._source_exists(arxiv_id):
            return "NO_SOURCE"

        logger.debug("No PDF found for %s, source exists and is not WDR, tried %s", arxiv_id.idv,
                     [str(ps_cache_pdf), str(current_pdf)])
        return "UNAVAIABLE"


    def is_withdrawn(self, arxiv_id: Identifier) -> bool:
        """Is a version is withdrawn?

        This will be the case if there is no source for a version or
        the source type in the abs file is 'I'.
        """
        return self._source_type(arxiv_id) == 'I'


    def _source_type(self, arxiv_id: Identifier) -> str:
        """Gets the source type for the arxiv_id current or
        arxiv_id.version from the current abs file

        This isn't great and if we are going to handle any other
        values from the abs we should have a metatdata object like in
        arxiv-browse.
        """
        abs_key = abs_path_current(arxiv_id)
        datelines = []
        with self.objstore.to_obj(abs_key).open('r') as fh:
            in_data = False
            for line in fh.readlines():
                line = line.strip()
                if not in_data:
                    if line.strip().startswith('\\\\'):
                        in_data = True
                        continue
                    else:
                        continue

                if line.startswith('Date'):
                    datelines.append(line)

                if line.strip().startswith('\\\\'):
                    break

        count, versions = ArticleStore._parse_version_entries(datelines)
        if arxiv_id.has_version:
            if len(versions) < arxiv_id.version:
                return '' #or raise Exception?
            return versions[arxiv_id.version-1]['source_type']
        else:
            return versions[-1]['source_type']


    @staticmethod
    def _parse_version_entries(version_entry_list: List) \
            -> Tuple[int, List[dict]]:
        """Parse the version entries from the arXiv .abs file.

        Based on arxiv-browse/browse/services/metadata.py commit 28a0317"""
        version_count = 0
        version_entries = list()
        for parsed_version_entry in version_entry_list:
            version_count += 1
            date_match = RE_DATE_COMPONENTS.match(parsed_version_entry)
            if not date_match:
                raise Exception('Could not extract date components from date line.')
            try:
                submitted_date = parser.parse(date_match.group('date'))
            except (ValueError, TypeError):
                raise Exception(f'Could not parse submitted date as datetime')

            source_type = date_match.group('source_type')
            ve = dict(
                raw=date_match.group(0),
                source_type=source_type,
                size_kilobytes=int(date_match.group('size_kilobytes')),
                submitted_date=submitted_date,
                version=version_count
            )
            version_entries.append(ve)

        return ( version_count, version_entries)


    def _source_exists(self, arxiv_id: Identifier) -> bool:
        res = self._versioned_or_current(arxiv_id)
        if not res:
            return False # does source exist or not for a non found paper?
        vnum, is_current = res

        parent = abs_path_current_parent(arxiv_id) if is_current else abs_path_orig_parent(arxiv_id)
        pattern = parent + '/' + arxiv_id.filename

        items = list(self.objstore.list(pattern))
        if len(items) > 1000:
            logger.warning("list of matches to is_withdrawn was %d, unexpectedly large", len(items))
            return True # strange but don't get into handling a huge list

        # does any obj key match with any extension?
        return any(map(lambda item: src_regex.match(item.name), items))


    def _versioned_or_current(self, arxiv_id: Identifier) -> Optional[Tuple[int, bool]]:
        current_ver = self.current_version(arxiv_id)
        if not current_ver:
            return None
        elif arxiv_id.has_version:
            current = arxiv_id.version == current_ver
            return (arxiv_id.version, current)
        else:
            return (current_ver, True)
