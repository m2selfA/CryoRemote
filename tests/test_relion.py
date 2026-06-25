from __future__ import annotations

from pathlib import PurePosixPath

from cryoremote_bundle.models import RemoteEntry
from cryoremote_bundle.relion import (
    build_flowchart_layout,
    choose_latest_job,
    classify_job_type,
    extract_tags,
    latest_completed_job,
    latest_refine_job,
    load_project_index,
    scan_project_index,
)


PIPELINE_STAR = """data_pipeline_general
_rlnPipeLineJobCounter 3

data_pipeline_processes
loop_
_rlnPipeLineProcessName #1
_rlnPipeLineProcessAlias #2
_rlnPipeLineProcessTypeLabel #3
_rlnPipeLineProcessStatusLabel #4
Refine3D/job010/ None relion.refine3d Running
PostProcess/job011/ None relion.postprocess Scheduled
Class2D/job012/ Screening relion.class2d Failed

data_pipeline_nodes
loop_
_rlnPipeLineNodeName #1
_rlnPipeLineNodeTypeLabel #2
_rlnPipeLineNodeTypeLabelDepth #3
Refine3D/job010/run_class001.mrc DensityMap 1
PostProcess/job011/postprocess.mrc DensityMap 1
Class2D/job012/run_it025_data.star ProcessData 1

data_pipeline_input_edges
loop_
_rlnPipeLineEdgeFromNode #1
_rlnPipeLineEdgeProcess #2
Refine3D/job010/run_class001.mrc PostProcess/job011/

data_pipeline_output_edges
loop_
_rlnPipeLineEdgeProcess #1
_rlnPipeLineEdgeToNode #2
Refine3D/job010/ Refine3D/job010/run_class001.mrc
PostProcess/job011/ PostProcess/job011/postprocess.mrc
Class2D/job012/ Class2D/job012/run_it025_data.star
"""


class FakeFS:
    def __init__(self):
        self._info: dict[PurePosixPath, RemoteEntry] = {}
        self._children: dict[PurePosixPath, list[RemoteEntry]] = {}
        self._text: dict[PurePosixPath, str] = {}

    def add_dir(self, path: str, *, mtime: float | None = None):
        target = PurePosixPath(path)
        self._info[target] = RemoteEntry(target, "directory", mtime=mtime)
        self._children.setdefault(target, [])
        if target.parent != target and target.parent in self._children:
            self._children[target.parent].append(self._info[target])

    def add_file(self, path: str, content: str = "", *, mtime: float | None = None):
        target = PurePosixPath(path)
        encoded = content.encode("utf-8")
        entry = RemoteEntry(target, "file", size=len(encoded), mtime=mtime)
        self._info[target] = entry
        self._children.setdefault(target.parent, [])
        self._children[target.parent].append(entry)
        self._text[target] = content

    def info(self, path: str | PurePosixPath) -> RemoteEntry:
        target = PurePosixPath(str(path))
        if target not in self._info:
            raise FileNotFoundError(target)
        return self._info[target]

    def ls(self, path: str | PurePosixPath) -> list[RemoteEntry]:
        target = PurePosixPath(str(path))
        return sorted(self._children.get(target, []), key=lambda entry: (entry.entry_type != "directory", entry.name.lower()))

    def read_text_head(self, path: str | PurePosixPath, limit: int = 65536, encoding: str = "utf-8") -> str:
        target = PurePosixPath(str(path))
        if target not in self._text:
            raise FileNotFoundError(target)
        return self._text[target][:limit]


def build_pipeline_fs() -> FakeFS:
    fs = FakeFS()
    for path in [
        "/proj",
        "/proj/Refine3D",
        "/proj/Refine3D/job010",
        "/proj/PostProcess",
        "/proj/PostProcess/job011",
        "/proj/Class2D",
        "/proj/Class2D/job012",
    ]:
        fs.add_dir(path)
    fs.add_file("/proj/default_pipeline.star", PIPELINE_STAR, mtime=50.0)
    fs.add_file("/proj/Refine3D/job010/run_class001.mrc", "map", mtime=10.0)
    fs.add_file("/proj/Refine3D/job010/run_half1_class001_unfil.mrc", "half1", mtime=10.0)
    fs.add_file("/proj/Refine3D/job010/run_half2_class001_unfil.mrc", "half2", mtime=10.0)
    fs.add_file("/proj/Refine3D/job010/RELION_JOB_EXIT_SUCCESS", "", mtime=11.0)
    fs.add_file("/proj/PostProcess/job011/postprocess.mrc", "post", mtime=20.0)
    fs.add_file("/proj/PostProcess/job011/model.cif", "data_model", mtime=20.0)
    fs.add_file("/proj/PostProcess/job011/note.txt", "#gold #best downstream", mtime=20.0)
    fs.add_file("/proj/Class2D/job012/run_it025_data.star", "data_", mtime=12.0)
    fs.add_file("/proj/Class2D/job012/run.err", "failed", mtime=12.0)
    return fs


def test_classify_job_type():
    assert classify_job_type(PurePosixPath("/proj/Refine3D/job042")) == "Refine3D"
    assert classify_job_type(PurePosixPath("/proj/PostProcess/job043")) == "PostProcess"
    assert classify_job_type(PurePosixPath("/proj/Other/job999")) is None


def test_choose_latest_job_prefers_highest_job_number():
    entries = [
        RemoteEntry(PurePosixPath("/proj/Refine3D/job002"), "directory", mtime=10),
        RemoteEntry(PurePosixPath("/proj/Refine3D/job017"), "directory", mtime=5),
        RemoteEntry(PurePosixPath("/proj/Refine3D/job010"), "directory", mtime=100),
    ]

    assert choose_latest_job(entries).path.name == "job017"


def test_load_project_index_builds_pipeline_graph():
    fs = build_pipeline_fs()

    project = load_project_index(fs, PurePosixPath("/proj"))

    assert project.source == "pipeline"
    assert {job.job_id for job in project.jobs} == {"Refine3D/job010/", "PostProcess/job011/", "Class2D/job012/"}

    refine = project.job_by_id("Refine3D/job010/")
    post = project.job_by_id("PostProcess/job011/")
    assert refine is not None and post is not None
    assert refine.state == "succeeded"
    assert "PostProcess/job011/" in refine.children
    assert post.parents == ("Refine3D/job010/",)
    assert post.artifacts.postprocess_map.name == "postprocess.mrc"
    assert post.artifacts.model_path.name == "model.cif"
    assert post.tags == ("gold", "best")


def test_latest_refine_and_completed_job_selection():
    fs = build_pipeline_fs()
    project = load_project_index(fs, PurePosixPath("/proj"))

    refine = latest_refine_job(project)
    completed = latest_completed_job(project)

    assert refine is not None
    assert refine.job_id == "PostProcess/job011/"
    assert completed is not None
    assert completed.job_id == "Refine3D/job010/"


def test_build_flowchart_layout_uses_parent_child_depths():
    fs = build_pipeline_fs()
    project = load_project_index(fs, PurePosixPath("/proj"))

    layout = {item.job_id: (item.column, item.row) for item in build_flowchart_layout(project)}

    assert layout["Refine3D/job010/"][0] == 0
    assert layout["Class2D/job012/"][0] == 0
    assert layout["PostProcess/job011/"][0] == 1


def test_scan_project_index_falls_back_without_pipeline():
    fs = FakeFS()
    fs.add_dir("/proj")
    fs.add_dir("/proj/Refine3D")
    fs.add_dir("/proj/Refine3D/job002")
    fs.add_file("/proj/Refine3D/job002/run_class001.mrc", "map", mtime=8.0)
    fs.add_file("/proj/Refine3D/job002/run.out", "running", mtime=8.0)

    project = scan_project_index(fs, PurePosixPath("/proj"))

    assert project.source == "scan"
    assert project.jobs[0].job_id == "Refine3D/job002/"
    assert project.jobs[0].artifacts.latest_map.name == "run_class001.mrc"
    assert project.jobs[0].state == "running"


def test_extract_tags_deduplicates_markers():
    assert extract_tags("#alpha #beta #alpha") == ("alpha", "beta")
