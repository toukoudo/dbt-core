import os
from dataclasses import dataclass
from typing import (
    Callable,
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    TypeVar,
    Union,
)

from pathspec import PathSpec  # type: ignore

from dbt import deprecations
from dbt.config import Project
from dbt.contracts.files import AnySourceFile, FilePath
from dbt.exceptions import DbtInternalError, ParsingError
from dbt_common.clients._jinja_blocks import ExtractWarning
from dbt_common.clients.jinja import BlockTag, extract_toplevel_blocks
from dbt_common.clients.system import find_matching


# What's the point of wrapping a SourceFile with this class?
# Could it be removed?
@dataclass
class FileBlock:
    file: AnySourceFile

    @property
    def name(self):
        base = os.path.basename(self.file.path.relative_path)
        name, _ = os.path.splitext(base)
        return name

    @property
    def contents(self):
        return self.file.contents

    @property
    def path(self):
        return self.file.path


# The BlockTag is used in Jinja processing
# Why do we have different classes where the only
# difference is what 'contents' returns?
@dataclass
class BlockContents(FileBlock):
    file: AnySourceFile  # if you remove this, mypy will get upset
    block: BlockTag

    @property
    def name(self):
        return self.block.block_name

    @property
    def contents(self):
        return self.block.contents


@dataclass
class FullBlock(FileBlock):
    file: AnySourceFile  # if you remove this, mypy will get upset
    block: BlockTag

    @property
    def name(self):
        return self.block.block_name

    @property
    def contents(self):
        return self.block.full_block


def filesystem_search(
    project: Project,
    relative_dirs: List[str],
    extension: str,
    ignore_spec: Optional[PathSpec] = None,
):
    ext = "[!.#~]*" + extension
    root = project.project_root
    file_path_list = []
    for result in find_matching(root, relative_dirs, ext, ignore_spec):
        if "searched_path" not in result or "relative_path" not in result:
            raise DbtInternalError("Invalid result from find_matching: {}".format(result))
        file_match = FilePath(
            searched_path=result["searched_path"],
            relative_path=result["relative_path"],
            modification_time=result["modification_time"],
            project_root=root,
        )
        file_path_list.append(file_match)

    return file_path_list


Block = Union[BlockContents, FullBlock]

BlockSearchResult = TypeVar("BlockSearchResult", BlockContents, FullBlock)

BlockSearchResultFactory = Callable[[AnySourceFile, BlockTag], BlockSearchResult]


class BlockSearcher(Generic[BlockSearchResult], Iterable[BlockSearchResult]):
    def __init__(
        self,
        source: List[FileBlock],
        allowed_blocks: Set[str],
        source_tag_factory: BlockSearchResultFactory,
    ) -> None:
        self.source = source
        self.allowed_blocks = allowed_blocks
        self.source_tag_factory: BlockSearchResultFactory = source_tag_factory

    def extract_blocks(self, source_file: FileBlock) -> Iterable[BlockTag]:
        # This is a bit of a hack to get the file path to the deprecation
        def wrap_handle_extract_warning(warning: ExtractWarning) -> None:
            self._handle_extract_warning(warning=warning, file=source_file.path.relative_path)

        try:
            blocks = extract_toplevel_blocks(
                source_file.contents,
                allowed_blocks=self.allowed_blocks,
                collect_raw_data=False,
                warning_callback=wrap_handle_extract_warning,
            )
            # this makes mypy happy, and this is an invariant we really need
            for block in blocks:
                assert isinstance(block, BlockTag)
                yield block

        except ParsingError as exc:
            if exc.node is None:
                exc.add_node(source_file)
            raise

    def _handle_extract_warning(self, warning: ExtractWarning, file: str) -> None:
        deprecations.warn("unexpected-jinja-block-deprecation", msg=warning.msg, file=file)

    def __iter__(self) -> Iterator[BlockSearchResult]:
        for entry in self.source:
            for block in self.extract_blocks(entry):
                yield self.source_tag_factory(entry.file, block)
