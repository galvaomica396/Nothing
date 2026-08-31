#[path = "../contracts_generated.rs"]
#[allow(dead_code)]
mod contracts_generated;

use std::error::Error;
use std::io::{self, Read, Write};

fn round_trip<T>(input: &[u8]) -> Result<Vec<u8>, serde_json::Error>
where
    T: serde::de::DeserializeOwned + serde::Serialize,
{
    let value = serde_json::from_slice::<T>(input)?;
    serde_json::to_vec(&value)
}

fn main() -> Result<(), Box<dyn Error>> {
    let mode = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "manifest".to_string());
    let mut input = Vec::new();
    io::stdin().read_to_end(&mut input)?;
    let output = match mode.as_str() {
        "manifest" => round_trip::<contracts_generated::AnalysisManifestV1>(&input)?,
        "resolution" => round_trip::<contracts_generated::ResolveMaskingReviewRequest>(&input)?,
        unexpected => return Err(format!("unsupported mode: {unexpected}").into()),
    };
    io::stdout().write_all(&output)?;
    Ok(())
}
