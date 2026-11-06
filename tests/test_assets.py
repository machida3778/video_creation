from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import contextlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from vg import assets, cli, run as runs
from vg.media import load, save
from vg.schemas import Brief, Manifest, Research, Route, Scene, Storyboard, Visual


ROOT = Path(__file__).resolve().parents[1]


def worker_module(name):
    spec = importlib.util.spec_from_file_location("synthetic_t2i_" + name, ROOT / "workers/t2i" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BatchAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run"
        self.run.mkdir()
        runs.write_state(self.run, {"stages": {}})
        for stage, value in [("brief", Brief(request="synthetic", topic="synthetic")),
                             ("route", Route(type_id="synthetic", confidence=1, reason="test")),
                             ("research", Research())]:
            save(self.run, stage, value)
        self.variants = [runs.new_variant(self.run) for _ in range(3)]
        for i, variant in enumerate(self.variants):
            save(variant, "storyboard", Storyboard(type_id="synthetic", scenes=[
                Scene(id="C1", line_ids=["L1"], component="ken_burns_image",
                      visual=Visual(kind="t2i", prompt=f"synthetic {i}", seed=i))]))

    def fake_worker(self, command, min_mem):
        self.assertEqual(min_mem, assets.T2I["min_mem_mb"])
        args = shlex.split(command)
        spec_path = Path(args[args.index("--spec") + 1].replace("/mapped", str(self.root), 1))
        jobs = json.loads(spec_path.read_text())["jobs"]
        self.assertEqual(len({j["output"] for j in jobs}), len(jobs))
        self.observed = jobs
        for job in jobs:
            self.assertTrue(Path(job["output"]).is_absolute())
            self.assertTrue(job["output"].startswith("/mapped/"))
            out = Path(job["output"].replace("/mapped", str(self.root), 1))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(f"synthetic image {job['seed']}".encode())

    def mapping(self, path):
        return str(path).replace(str(self.root), "/mapped", 1)

    def test_all_variants_one_job_and_resume(self):
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(assets, "_run_on_lab_gpu", side_effect=self.fake_worker) as worker:
            self.assertEqual(cli.main(["assets", str(self.run), "--all"]), 0)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(len(self.observed), 3)
            for index, variant in enumerate(self.variants):
                board, manifest = load(variant, "storyboard"), load(variant, "manifest")
                self.assertEqual(board.scenes[0].visual.ref, "img_C1")
                image = manifest.assets[0]
                self.assertEqual(image.params["seed"], index)
                self.assertEqual((variant / image.path).read_bytes(), f"synthetic image {index}".encode())
                self.assertTrue(image.commercial_ok)
                self.assertEqual(runs.read_state(variant)["stages"]["assets"]["status"], "done")
            cli.main(["assets", str(self.run), "--all"])
            self.assertEqual(worker.call_count, 1)
            # Only a missing variant image needs regeneration; stale manifest entry is replaced.
            (self.variants[1] / load(self.variants[1], "manifest").assets[0].path).unlink()
            cli.main(["assets", str(self.run), "--all"])
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(len(self.observed), 1)
            self.assertEqual(len(load(self.variants[1], "manifest").assets), 1)

    def write_covers(self, variant, prompt):
        covers = {"options": [
            {"id": "own", "template": "reveal", "text": "3人に1人", "prompt": prompt, "line_id": "L1",
             "palette": "yellow", "ai_generated": True, "photorealistic": False},
            {"id": "scene", "template": "question", "text": "なぜ？", "image": "img_C1", "line_id": "L1",
             "palette": "white", "ai_generated": True, "photorealistic": False}]}
        (variant / "covers.json").write_text(json.dumps(covers, ensure_ascii=False), encoding="utf-8")

    def test_cover_only_images_are_generated_with_the_scenes(self):
        variant = self.variants[0]
        target = str(self.run) + ":" + variant.name
        self.write_covers(variant, "three chairs, one empty, close-up")
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(assets, "_run_on_lab_gpu", side_effect=self.fake_worker) as worker:
            cli.main(["assets", target])
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(sorted(job["id"] for job in self.observed), ["cover_own", "img_C1"])
            cover = next(job for job in self.observed if job["id"] == "cover_own")
            self.assertEqual((cover["width"], cover["height"]), (1088, 1920))
            first = {a.id: a for a in load(variant, "manifest").assets}["cover_own"]
            self.assertRegex(first.path, r"^assets/images/cover_own-[0-9a-f]{10}\.png$")
            self.assertEqual((first.commercial_ok, first.params["prompt"], first.params["seed"]),
                             (True, "three chairs, one empty, close-up", cover["seed"]))
            self.assertEqual(load(variant, "covers").options[0].image, "cover_own")
            # Unchanged prompt: nothing to draw again; the seed does not depend on the option order.
            covers = json.loads((variant / "covers.json").read_text(encoding="utf-8"))
            covers["options"].reverse()
            (variant / "covers.json").write_text(json.dumps(covers, ensure_ascii=False), encoding="utf-8")
            cli.main(["assets", target])
            self.assertEqual(worker.call_count, 1)
            # A new prompt writes a new file; the earlier image stays on disk.
            self.write_covers(variant, "a single empty office chair under a spotlight")
            cli.main(["assets", target])
            self.assertEqual(worker.call_count, 2)
            self.assertEqual([job["id"] for job in self.observed], ["cover_own"])
        second = {a.id: a for a in load(variant, "manifest").assets}["cover_own"]
        self.assertNotEqual(second.path, first.path)
        self.assertTrue((variant / first.path).is_file() and (variant / second.path).is_file())
        self.assertEqual(second.params["prompt"], "a single empty office chair under a spotlight")

    def test_interrupted_cover_job_resumes_without_redrawing(self):
        variant = self.variants[0]
        self.write_covers(variant, "three chairs, one empty, close-up")
        def skipping_worker(command, min_mem):  # like workers/t2i/generate.py: existing files are not redrawn
            args = shlex.split(command)
            spec = Path(args[args.index("--spec") + 1].replace("/mapped", str(self.root), 1))
            for job in json.loads(spec.read_text())["jobs"]:
                out = Path(job["output"].replace("/mapped", str(self.root), 1))
                if not out.is_file():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"new drawing")

        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(assets, "_run_on_lab_gpu", side_effect=skipping_worker):
            jobs = assets.cover_jobs(variant)
            # The worker wrote the image, then the session died before the manifest was saved.
            out = variant / "assets/images" / f"{jobs[0]['file']}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"finished cover")
            cli.main(["assets", str(self.run) + ":" + variant.name])
        image = {a.id: a for a in load(variant, "manifest").assets}["cover_own"]
        self.assertEqual((variant / image.path).read_bytes(), b"finished cover")

    def test_plain_variant_cli_and_batch_failure_states(self):
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(assets, "_run_on_lab_gpu", side_effect=self.fake_worker):
            cli.main(["assets", str(self.run) + ":v1"])
        self.assertEqual(len(self.observed), 1)
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(assets, "_run_on_lab_gpu", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                cli.main(["assets", str(self.run), "--all"])
        self.assertTrue(all(runs.read_state(v)["stages"]["assets"]["status"] == "failed" for v in self.variants))
        with self.assertRaisesRegex(ValueError, "parent run"):
            cli.main(["assets", str(self.variants[0]), "--all"])

    def test_remote_command_cache_optional_and_quoted(self):
        for cache in ["", "/local disk/hub"]:
            with self.subTest(cache=cache), patch.object(assets.site, "load", return_value={"LAB_LOCAL_HF_CACHE": cache}), \
                    patch.object(assets, "idle_gpus", return_value=[(24576, "gpu-test", 0)]), \
                    patch.object(assets, "LOCKS", self.root / "locks"), \
                    patch.object(assets, "remote", return_value="/synthetic repo"), \
                    patch.object(assets.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as process, \
                    contextlib.redirect_stdout(io.StringIO()):
                assets._run_on_lab_gpu("worker --spec /synthetic", 24000)
                command = process.call_args.args[0][-1]
                self.assertIn("bash scripts/lab/gpu_free.sh 0 &&", command)
                self.assertIn("CUDA_VISIBLE_DEVICES=0", command)
                self.assertEqual("workers/t2i/cache.py" in command, bool(cache))
                if cache:
                    self.assertIn("--cache '/local disk/hub'", command)
        with patch.object(assets.site, "load", return_value={"LAB_LOCAL_HF_CACHE": "relative"}):
            with self.assertRaisesRegex(ValueError, "absolute"):
                assets._run_on_lab_gpu("worker", 24000)


class WorkerProtocolTests(unittest.TestCase):
    def test_multiple_output_directories_load_once_and_legacy_out(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, {"torch": MagicMock()}):
            worker = worker_module("generate")
            worker.torch.cuda.max_memory_allocated.return_value = 0
            root = Path(temporary)
            targets = [root / v / "img_C1.png" for v in ("v1", "v2")]
            jobs = [dict(id="img_C1", prompt="synthetic", seed=i, width=16, height=16, output=str(path))
                    for i, path in enumerate(targets)]
            spec = root / "jobs.json"
            spec.write_text(json.dumps(dict(model="Tongyi-MAI/Z-Image-Turbo", jobs=jobs)))
            pipe = MagicMock()
            pipe.return_value.images[0].save.side_effect = lambda path: Path(path).write_bytes(b"synthetic")
            with patch.object(sys, "argv", ["generate.py", "--spec", str(spec)]), \
                    patch.object(worker, "load", return_value=pipe) as model, \
                    contextlib.redirect_stdout(io.StringIO()):
                worker.main()
                self.assertEqual(model.call_count, 1)
                self.assertEqual(pipe.call_count, 2)
                self.assertTrue(all(p.is_file() and p.with_suffix(".json").is_file() for p in targets))
                worker.main()
                self.assertEqual(model.call_count, 1)
            self.assertEqual(worker.output_path({"id": "old"}, str(root)), root / "old.png")
            for job, out in [({"output": "relative.png"}, None), ({"id": "old"}, None)]:
                with self.assertRaises(ValueError):
                    worker.output_path(job, out)


class WeightCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = worker_module("cache")
        self.model = "synthetic/model"
        self.folder = "models--synthetic--model"
        self.source, self.target = self.root / "shared", self.root / "local"
        repository = self.source / self.folder
        (repository / "refs").mkdir(parents=True)
        (repository / "refs/main").write_text("revision1")
        snapshot = repository / "snapshots/revision1"
        snapshot.mkdir(parents=True)
        (snapshot / "model_index.json").write_text("{}")
        (repository / "blobs").mkdir()
        (repository / "blobs/weights").write_bytes(b"synthetic weights")
        (snapshot / "weights.bin").symlink_to("../../blobs/weights")

    def fake_rsync(self, command, **kwargs):
        self.assertEqual(command[:4], ["rsync", "-a", "--copy-links", "--"])
        shutil.copytree(command[-2], command[-1], dirs_exist_ok=True, symlinks=False)

    def test_sync_is_idempotent_and_independent_of_shared_blobs(self):
        with patch.object(self.cache.subprocess, "run", side_effect=self.fake_rsync) as copy:
            self.assertEqual(self.cache.stage_snapshot(self.source, self.target, self.model), self.target)
            self.cache.stage_snapshot(self.source, self.target, self.model)
            self.assertEqual(copy.call_count, 1)
        weights = self.target / self.folder / "snapshots/revision1/weights.bin"
        self.assertFalse(weights.is_symlink())
        shutil.rmtree(self.source)
        self.assertEqual(weights.read_bytes(), b"synthetic weights")
        self.assertEqual((self.target / self.folder / "refs/main").read_text(), "revision1")

    def test_concurrent_copy_and_partial_recovery(self):
        partial = self.target / self.folder / "snapshots/revision1"
        partial.mkdir(parents=True)
        (partial / "incomplete").write_bytes(b"partial")
        def slow_copy(*args, **kwargs):
            time.sleep(0.02)
            return self.fake_rsync(*args, **kwargs)
        with patch.object(self.cache.subprocess, "run", side_effect=slow_copy) as copy, \
                ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.cache.stage_snapshot, self.source, self.target, self.model) for _ in range(2)]
            self.assertEqual([f.result(timeout=5) for f in futures], [self.target, self.target])
            self.assertEqual(copy.call_count, 1)
        self.assertFalse((partial / "incomplete").exists())

    def test_failed_copy_releases_lock_and_does_not_publish_snapshot(self):
        with patch.object(self.cache.subprocess, "run", side_effect=RuntimeError("synthetic failed copy")):
            with self.assertRaisesRegex(RuntimeError, "failed copy"):
                self.cache.stage_snapshot(self.source, self.target, self.model)
        self.assertFalse((self.target / self.folder / "snapshots/revision1/model_index.json").exists())
        with patch.object(self.cache.subprocess, "run", side_effect=self.fake_rsync):
            self.cache.stage_snapshot(self.source, self.target, self.model, timeout=0)

    def test_wrapper_rechecks_gpu_after_copy_and_sets_worker_cache(self):
        order = []
        env = {"CUDA_VISIBLE_DEVICES": "2"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(sys, "argv", ["cache.py", "--cache", str(self.target), "--model", self.model,
                                           "--", "worker", "--spec", "synthetic.json"]), \
                patch.object(self.cache, "stage_snapshot", side_effect=lambda *a: order.append("copy") or self.target), \
                patch.object(self.cache.subprocess, "run", side_effect=lambda *a, **k: order.append("gpu_free")) as free, \
                patch.object(self.cache.os, "execvpe", side_effect=lambda *a: order.append("exec")) as execute:
            self.cache.main()
            self.assertEqual(order, ["copy", "gpu_free", "exec"])
            self.assertEqual(free.call_args.args[0][-1], "2")
            self.assertEqual(execute.call_args.args[:2], ("worker", ["worker", "--spec", "synthetic.json"]))
            self.assertEqual(execute.call_args.args[2]["HF_HUB_CACHE"], str(self.target))
            self.assertEqual(execute.call_args.args[2]["HF_HUB_OFFLINE"], "1")


if __name__ == "__main__":
    unittest.main()
