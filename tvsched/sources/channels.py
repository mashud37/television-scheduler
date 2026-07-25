from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    canonical: str
    source: str
    api_id: str


ZDF_CHANNELS = (
    Channel("ZDF", "zdf", "zdf"),
    Channel("ZDFneo", "zdf", "zdfneo"),
    Channel("ZDFinfo", "zdf", "zdfinfo"),
    Channel("3sat", "zdf", "3sat"),
    Channel("PHOENIX", "zdf", "phoenix"),
    Channel("ARTE", "zdf", "arte"),
)

ARD_CHANNELS = (
    Channel("ARD", "ard", "daserste"),
    Channel("BR", "ard", "br"),
    Channel("HR", "ard", "hr"),
    Channel("MDR", "ard", "mdr"),
    Channel("NDR", "ard", "ndr"),
    Channel("Radio Bremen", "ard", "radiobremen"),
    Channel("RBB", "ard", "rbb"),
    Channel("SWR/SR", "ard", "sr"),
    Channel("SWR/SR", "ard", "swr"),
    Channel("WDR", "ard", "wdr"),
    Channel("ONE", "ard", "one"),
    Channel("KiKA", "ard", "kika"),
    Channel("ARD-alpha", "ard", "alpha"),
    Channel("tagesschau24", "ard", "tagesschau24"),
)

ALL_CHANNELS = ZDF_CHANNELS + ARD_CHANNELS

ARD_ID_TO_CANONICAL = {c.api_id: c.canonical for c in ARD_CHANNELS}
ZDF_ID_TO_CANONICAL = {c.api_id: c.canonical for c in ZDF_CHANNELS}

CANONICAL_NAMES = sorted({c.canonical for c in ALL_CHANNELS})


def unpriced_channels(channel_prior: dict) -> list[str]:
    """Canonical names this collector can emit that carry no explicit channel_prior weight.

    Such channels silently fall back to default_channel_score, which z-scores to zero
    within its group and removes the strongest ranking signal. The caller is expected to
    surface the result rather than ignore it.

    Args:
        channel_prior: The channel_prior mapping from the loaded RankerConfig.

    Returns:
        Canonical channel names absent from channel_prior, sorted.
    """
    return sorted(n for n in CANONICAL_NAMES if n not in (channel_prior or {}))
