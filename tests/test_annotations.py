"""Independent generated operator files; optional failures never hide valid pdata."""

import pytest

from bruker_nmr.domain.integrals import add_region, assign_reference, integral_snapshot
from bruker_nmr.io.source import Source
from tests.synthetic_bruker import experiment, status


def annotated(root, *, scale=2, intscl=.5, step=1):
    exp, proc, a, p = experiment(root, scale=scale)
    p['SW_p'] = step * 300 * 8
    if intscl is not None:
        p['INTSCL'] = intscl
    status(proc / 'procs', p)
    bounds = [(12, 12 - 2 * step), (12 - 2 * step, 12 - 4 * step),
              (12 - 6 * step, 12 - 7 * step)]
    (proc / 'intrng').write_text('1D 3 #regions in PPM\n# High Low Bias Slope\n' +
                               ''.join(f'{high} {low} 0 0\n' for high, low in bounds))
    # Truth from independent fixture integer values: 2-4, 6+8 and 14.
    counts = [-2, 14, 14]
    table = ''.join(f'{i+1} {high:.5f} {low:.5f} {v*.5:.5f}\n'
                    for i, ((high, low), v) in enumerate(zip(bounds, counts)))
    (proc / 'integrals.txt').write_text('# Number Region Integral\n' + table)
    (proc / 'peaklist.xml').write_text(
        '<PeakList><PeakList1D><Peak1D F1="10.0" intensity="3" type="0"/>'
        '<Peak1D F1="9.0" intensity="-2" type="1"/></PeakList1D></PeakList>')
    return exp, proc, p


def read(exp):
    with Source(exp) as source:
        return source.read_processed()


@pytest.mark.parametrize('scale', [-3, 4])
@pytest.mark.parametrize('step', [.5, 2])
def test_nc_grid_conversion_and_all_peak_records(tmp_path, scale, step):
    exp, _, _ = annotated(tmp_path, scale=scale, step=step)
    data = read(exp)
    state = data.annotations.integrals
    assert state.normalization.kind == 'imported'
    assert state.normalization.anchor_id is None
    assert state.normalization.scale == pytest.approx(.5 * 2.**(-scale) / step)
    assert [r.value for r in integral_snapshot(state, data.integration)] == pytest.approx([-1, 7, 7])
    assert len(data.annotations.peaks) == 2  # Neither sign nor type is an implicit filter.
    assert not data.annotations.issues


def test_coordinate_matching_not_row_number_and_intscl_one(tmp_path):
    exp, proc, _ = annotated(tmp_path, intscl=1)
    lines = (proc / 'integrals.txt').read_text().splitlines()
    # Correct values for INTSCL=1, reordered with arbitrary printed row numbers.
    (proc / 'integrals.txt').write_text('8 6 5 14.00000\n9 12 10 -2.00000\n3 10 8 14.00000\n')
    data = read(exp)
    assert data.annotations.integrals.normalization.kind == 'imported'
    assert [r.operator_value for r in integral_snapshot(data.annotations.integrals, data.integration)] == [-2, 14, 14]
    assert len(lines) == 4


def test_missing_intscl_common_scale_and_conflicting_table(tmp_path):
    exp, proc, _ = annotated(tmp_path, intscl=None)
    data = read(exp)
    assert data.annotations.integrals.normalization.kind == 'imported'
    assert 'table' in data.annotations.integrals.normalization.provenance
    (proc / 'integrals.txt').write_text('1 12 10 -1.00000\n2 10 8 99.00000\n3 6 5 7.00000\n')
    data = read(exp)
    assert data.annotations.integrals.normalization.kind == 'unverified'
    assert 'integrals:source_conflict' in data.annotations.issues
    added = add_region(data.annotations.integrals, data.integration, 8, 7)
    assert added.normalization.kind == 'unverified'


def test_geometry_conflict_and_ambiguous_matching(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    (proc / 'integrals.txt').write_text('1 11 9 -1.00000\n2 10 8 7.00000\n3 6 5 7.00000\n')
    data = read(exp)
    assert data.annotations.integrals.normalization.kind == 'unverified'
    assert data.annotations.integrals.regions[0].imported_bounds == (12, 10)


def test_nonzero_correction_retained_never_silently_zeroed(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    (proc / 'intrng').write_text('# regions PPM\n12 10 1 0\n10 8 0 0\n6 5 0 0\n')
    data = read(exp)
    state = data.annotations.integrals
    assert state.normalization.kind == 'unverified'
    assert state.regions[0].bias == 1
    assert all(row.value is None for row in integral_snapshot(state, data.integration))
    reference = assign_reference(state, data.integration, state.regions[1].id, 2)
    assert integral_snapshot(reference, data.integration)[0].value is None
    assert integral_snapshot(reference, data.integration)[1].value == pytest.approx(2)


def test_table_only_reduced_precision_and_no_annotations_fresh_state(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    (proc / 'intrng').unlink()
    data = read(exp)
    assert len(data.annotations.integrals.regions) == 3
    assert 'integrals:table_geometry_reduced_precision' in data.annotations.issues
    for name in ('integrals.txt', 'peaklist.xml'):
        (proc / name).unlink()
    data = read(exp)
    assert not data.annotations.integrals.regions
    assert data.annotations.integrals.normalization.kind == 'absent'
    manual = add_region(data.annotations.integrals, data.integration, 10, 8)
    assert manual.normalization.protons == 1


@pytest.mark.parametrize('content', [
    '<PeakList><Peak1D',
    '<!DOCTYPE a [<!ENTITY x "bad">]><PeakList/>',
    '<PeakList unit="Hz"><Peak1D F1="9" intensity="1" type="0"/></PeakList>',
    '<Unexpected/>',
])
def test_bad_xml_does_not_hide_integrals_or_spectrum(tmp_path, content):
    exp, proc, _ = annotated(tmp_path)
    (proc / 'peaklist.xml').write_text(content)
    data = read(exp)
    assert len(data.real) == 8 and len(data.annotations.integrals.regions) == 3
    assert not data.annotations.peaks
    assert 'peaklist.xml:unreadable_or_unsupported' in data.annotations.issues


def test_optional_invalid_rows_limits_units_and_reference_required(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    (proc / 'intrng').write_text('# regions PPM\n12 10 0 0\n100 99 0 0\nnan 8 0 0\n7 7 0 0\n')
    (proc / 'integrals.txt').unlink()
    data = read(exp)
    assert len(data.annotations.integrals.regions) == 1
    assert 'intrng:invalid_rows:3' in data.annotations.issues
    (proc / 'intrng').write_text('# regions Hz\n12 10 0 0\n')
    data = read(exp)
    assert not data.annotations.integrals.regions
    assert data.annotations.integrals.normalization.kind == 'unverified'
    (proc / 'intrng').write_bytes(b' ' * (2 * 1024 * 1024 + 1))
    data = read(exp)
    assert 'intrng:unreadable_or_unsupported' in data.annotations.issues
    assert len(data.annotations.peaks) == 2


def test_invalid_peaks_retained_valid_entries_and_unsupported_binary_notice(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    (proc / 'peaklist.xml').write_text('<PeakList>'
        '<Peak1D F1="9" intensity="1" type="0"/>'
        '<Peak1D F1="999" intensity="1" type="0"/>'
        '<Peak1D F1="8" intensity="nan" type="0"/></PeakList>')
    data = read(exp)
    assert len(data.annotations.peaks) == 1
    assert 'peaklist.xml:invalid_rows:2' in data.annotations.issues
    (proc / 'peaklist.xml').unlink()
    (proc / 'peakrng').write_text('independent ranges, not peak records')
    assert 'peaks:binary_or_ranges_not_imported' in read(exp).annotations.issues
