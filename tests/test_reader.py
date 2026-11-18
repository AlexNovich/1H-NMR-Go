"""Independent binary/ppm truth, malformed inputs and source ownership."""
import shutil

import numpy as np
import pytest

from bruker_nmr.io.errors import InputError
from bruker_nmr.io.source import Limits, Source
from bruker_nmr.io.status import read_status
from tests.synthetic_bruker import VALUES, experiment, status


@pytest.mark.parametrize("dtype", [0, 2])
@pytest.mark.parametrize("endian", [0, 1])
@pytest.mark.parametrize("scale", [-3, 4])
@pytest.mark.parametrize("padded", [False, True])
def test_exact_binary_and_ppm(tmp_path, dtype, endian, scale, padded):
    exp, proc, _, _ = experiment(tmp_path, dtype=dtype, endian=endian, scale=scale, padded=padded)
    original = (proc / "1r").read_bytes()
    with Source(exp) as source:
        candidate, = source.discover()
        assert candidate.processed_available and candidate.raw_available
        assert candidate.nucleus == "1H" and candidate.dimension == 1
        data = source.read_processed()
    assert np.array_equal(data.raw_real, VALUES)
    assert np.array_equal(data.real, np.array(VALUES, dtype=float) * 2.0**scale)
    assert np.array_equal(data.imaginary, data.real)
    assert np.array_equal(data.complex_data.real, data.real)
    assert np.array_equal(data.ppm, [12 - k for k in range(8)])
    assert data.ppm[-1] == 5  # Last bin is not the nominal endpoint 4 ppm.
    assert data.raw_real.dtype.kind == ("i" if dtype == 0 else "f")
    for array in [data.raw_real, data.real, data.imaginary, data.ppm]:
        with pytest.raises(ValueError):
            array.flags.writeable = True
    assert (proc / "1r").read_bytes() == original
    with pytest.raises(InputError, match="Source closed"):
        source.discover()


def test_missing_imaginary_and_direct_pdata(tmp_path):
    exp, proc, _, _ = experiment(tmp_path, imaginary=False)
    for root in [exp, proc, proc.parent]:
        with Source(root) as source:
            data = source.read_processed()
            assert not data.can_phase
            assert data.imaginary is None
            assert data.annotation_names == ()
            with pytest.raises(InputError, match="Imaginary unavailable"):
                _ = data.complex_data


@pytest.mark.parametrize("field,value", [
    ("SI", 2**30), ("SI", 8.5), ("SI", 0), ("DTYPP", 1), ("BYTORDP", 2),
    ("NC_proc", 1000), ("SF", 0), ("OFFSET", float("nan")), ("SW_p", -1),
    ("PPARMOD", 1), ("FT_mod", 0), ("STSR", 1), ("STSI", 4), ("XDIM", 3),
])
def test_invalid_processed_status(tmp_path, field, value):
    exp, proc, _, p = experiment(tmp_path)
    p[field] = value
    status(proc / "procs", p)
    with Source(exp) as source:
        candidate, = source.discover()
        assert not candidate.processed_available
        assert candidate.raw_available
        with pytest.raises(InputError):
            source.read_processed()


@pytest.mark.parametrize("name", ["acqus", "procs"])
def test_status_no_nonstatus_fallback(tmp_path, name):
    exp, proc, _, _ = experiment(tmp_path)
    path = exp / name if name == "acqus" else proc / name
    path.rename(path.with_name("acqu" if name == "acqus" else "proc"))
    with Source(exp) as source:
        candidate, = source.discover()
        assert not candidate.processed_available
        assert candidate.raw_available == (name == "procs")
        with pytest.raises(InputError):
            source.read_processed()


@pytest.mark.parametrize("evidence", ["ser", "acqu2s", "2rr"])
def test_dimension_evidence(tmp_path, evidence):
    exp, proc, _, _ = experiment(tmp_path)
    target = proc if evidence == "2rr" else exp
    (target / evidence).write_bytes(b"independent")
    with Source(exp) as source:
        assert not source.discover()[0].processed_available
        with pytest.raises(InputError, match="Unsupported dimension"):
            source.read_processed()


@pytest.mark.parametrize("key,value", [("NUC1", "13C"), ("PARMODE", 1), ("PARMODE", -1)])
def test_unsupported_acquisition(tmp_path, key, value):
    exp, _, a, _ = experiment(tmp_path)
    a[key] = value
    status(exp / "acqus", a)
    with Source(exp) as source:
        c, = source.discover()
        assert not c.processed_available and not c.raw_available
        with pytest.raises(InputError):
            source.read_processed()


@pytest.mark.parametrize("kind", ["truncated", "extra", "partial_value", "nonzero_padding", "nan"])
def test_invalid_binary(tmp_path, kind):
    exp, proc, _, _ = experiment(tmp_path, dtype=2)
    path = proc / "1r"
    data = path.read_bytes()
    if kind == "truncated":
        path.write_bytes(data[:-8])
    elif kind == "partial_value":
        path.write_bytes(data[:-1])
    elif kind == "extra":
        path.write_bytes(data + bytes(8))
    elif kind == "nonzero_padding":
        path.write_bytes(data + b"x" + bytes(1024-len(data)-1))
    else:
        import struct
        path.write_bytes(struct.pack("<d", float("nan")) + data[8:])
    with Source(exp) as source:
        with pytest.raises(InputError):
            source.read_processed()


def test_multiple_choices_and_raw_only(tmp_path):
    exp, proc, _, _ = experiment(tmp_path)
    shutil.copytree(proc, proc.with_name("2"))
    with Source(exp) as source:
        records = source.discover()
        assert len(records) == 2
        with pytest.raises(InputError, match="Explicit selection required"):
            source.read_processed()
        assert source.read_processed(records[1].candidate_id).can_phase
        with pytest.raises(InputError, match="Unknown selection"):
            source.read_processed("unknown")
    shutil.rmtree(exp / "pdata")
    with Source(exp) as source:
        c, = source.discover()
        assert c.raw_available and not c.processed_available


def test_candidate_limit(tmp_path):
    exp, proc, _, _ = experiment(tmp_path)
    shutil.copytree(proc, proc.with_name("2"))
    with Source(exp, Limits(max_candidates=1)) as source:
        with pytest.raises(InputError, match="Candidate count limit"):
            source.discover()


def test_jcamp_arrays_strings_encoding_and_comments(tmp_path):
    path = tmp_path / "acqus"
    path.write_bytes(("##TITLE= Independent fixture\n$$ generated\n\n"
                      "##$NUC1= <1H>\n##$D= (0..2)\n0.1 0.2\n0.3\n"
                      "##$LABEL= <café\nsecond line>\n##END=\n").encode("cp1252"))
    parsed = read_status(path)
    assert parsed["D"] == [.1, .2, .3]
    assert parsed["LABEL"] == "café\nsecond line"
    assert "_coreheader" not in parsed


@pytest.mark.parametrize("text", [
    "##$A= <unterminated\n", "##$D= (0..3)\n1 2\n", "##$D= (0..9999999999)\n",
    "##$SI= 8\n##$SI= 16\n", "##$bad parameter= 3\n", "random payload\n",
])
def test_malformed_jcamp_bounded(tmp_path, text):
    path = tmp_path / "acqus"
    path.write_text(text)
    with pytest.raises(InputError):
        read_status(path)


def test_corrupt_imaginary_does_not_silently_disable_phase(tmp_path):
    exp, proc, _, _ = experiment(tmp_path)
    (proc / "1i").write_bytes(b"short")
    with Source(exp) as source:
        assert not source.discover()[0].processed_available
        with pytest.raises(InputError):
            source.read_processed()


def test_unsupported_raw_keeps_valid_operator_mode(tmp_path):
    exp, _, a, _ = experiment(tmp_path)
    a["AQ_mod"] = 1
    status(exp / "acqus", a)
    with Source(exp) as source:
        candidate, = source.discover()
        assert candidate.processed_available and not candidate.raw_available
        assert source.read_processed().can_phase


def test_raw_nan_capability_and_annotation_names(tmp_path):
    import struct
    exp, proc, _, _ = experiment(tmp_path, dtype=2)
    raw = (exp / "fid").read_bytes()
    (exp / "fid").write_bytes(struct.pack("<d", float("nan")) + raw[8:])
    for name in ["intrng", "integrals.txt", "peaklist.xml"]:
        (proc / name).write_text("Independent annotation placeholder; parsing is Session 05")
    with Source(exp) as source:
        candidate, = source.discover()
        assert not candidate.raw_available
        assert candidate.processed_available
        data = source.read_processed()
        assert set(data.annotation_names) == {"intrng", "integrals.txt", "peaklist.xml"}


def test_parser_count_and_encoding_errors(tmp_path):
    path = tmp_path / "acqus"
    for payload in [b"##$D= (0..1) 1 2 3\n", b"\x81", b"##$A= 1\x00\n"]:
        path.write_bytes(payload)
        with pytest.raises(InputError):
            read_status(path)


def test_metadata_limits_and_sanitized_errors(tmp_path):
    path = tmp_path / "acqus"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(InputError, match="Status size limit") as error:
        read_status(path)
    assert str(tmp_path) not in str(error.value)


def test_packaged_independent_example_zip(tmp_path):
    from bruker_nmr.demo import make_demo
    from bruker_nmr.demo_input import write_demo_input
    path = tmp_path / "synthetic-example.zip"
    write_demo_input(path)
    with Source(path) as source:
        data = source.read_processed()
        assert not data.can_phase
        assert data.raw_real.dtype.kind == "f"
        assert np.array_equal(data.real, make_demo().amplitude[::-1])
        assert data.ppm[0] == 12
        assert data.ppm[-1] == pytest.approx(-1, abs=1e-12)
    with pytest.raises(FileExistsError):
        write_demo_input(path)
