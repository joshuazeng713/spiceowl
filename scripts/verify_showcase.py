"""Recompute the visual guide's spectra from published samples (requires NumPy)."""
import argparse
import json
from pathlib import Path
import numpy as np


def verify(path):
    data = json.loads(Path(path).read_text())
    reports = []
    for name, row in data['cases'].items():
        y = np.asarray(row['vout_v'], dtype=float)
        assert len(y) == data['sample_count'] == 4096 and np.isfinite(y).all()
        power = np.abs(np.fft.rfft(y - y.mean())) ** 2
        power[0] = 0
        tone_bin = int(np.argmax(power))
        assert tone_bin == 1811
        assert tone_bin * data['sample_rate_hz'] / len(y) == data['fundamental_hz']
        signal = power[tone_bin]
        power[tone_bin] = 0
        sndr = float(10 * np.log10(signal / power.sum()))
        sfdr = float(10 * np.log10(signal / power.max()))
        assert abs(sndr - row['sndr_db']) < 0.01
        assert abs(sfdr - row['sfdr_db']) < 0.01
        reports.append({'case': name, 'samples': len(y), 'tone_bin': tone_bin,
                        'sndr_db': sndr, 'sfdr_db': sfdr,
                        'scope': 'deterministic sample-and-hold; not ADC qualification'})
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples', type=Path)
    print(json.dumps(verify(parser.parse_args().samples), indent=2))
