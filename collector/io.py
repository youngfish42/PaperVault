import gzip
import json
import os


def _to_gz_path(path):
    """统一将 .jsonl 路径转为 .jsonl.gz 路径。"""
    if path.endswith(".jsonl") and not path.endswith(".jsonl.gz"):
        return path + ".gz"
    return path


def load_cache(path):
    """读取缓存。优先从 gzip 加载，回退到旧版纯文本 JSONL。"""
    gz_path = _to_gz_path(path)
    if os.path.exists(gz_path):
        data = {}
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    paper = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON on line {line_num} of {gz_path}: {e}")
                if "conf" not in paper or not isinstance(paper["conf"], str):
                    raise ValueError(
                        f"Missing or invalid 'conf' field on line {line_num} of {gz_path}"
                    )
                conf = paper.pop("conf")
                if conf not in data:
                    data[conf] = []
                data[conf].append(paper)
        return data
    # 兼容旧版纯文本 JSONL
    if os.path.exists(path):
        print(f"[!] Loading from legacy {path}. Consider migrating to gzip manually.")
        data = {}
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    paper = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON on line {line_num} of {path}: {e}")
                if "conf" not in paper or not isinstance(paper["conf"], str):
                    raise ValueError(
                        f"Missing or invalid 'conf' field on line {line_num} of {path}"
                    )
                conf = paper.pop("conf")
                if conf not in data:
                    data[conf] = []
                data[conf].append(paper)
        return data
    return {}


def save_cache(path, data):
    """将 dict[conf_name] -> list[paper_dict] 写入 gzip JSONL。"""
    gz_path = _to_gz_path(path)
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        for conf, papers in data.items():
            for paper in papers:
                record = dict(paper)
                record["conf"] = conf
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
