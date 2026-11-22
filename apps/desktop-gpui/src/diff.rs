//! Word-level diff between two texts, for the proofreading review.
//!
//! The backend keeps the raw ASR text of every segment DeepSeek corrected
//! (``original_text``) next to the corrected text, so the client can show which
//! words actually changed. A segment is a sentence, not a chapter, so a plain
//! longest-common-subsequence over whitespace-separated words is exact and
//! cheap.

/// What happened to one run of words between the two texts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DiffKind {
    /// Present in both, unchanged.
    Equal,
    /// Present only in the original: removed by the correction.
    Removed,
    /// Present only in the corrected text: added by the correction.
    Added,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DiffRun {
    pub kind: DiffKind,
    pub text: String,
}

/// Split `before` and `after` into whitespace-separated words and return the
/// minimal edit sequence as runs, in reading (logical) order.
pub fn word_diff(before: &str, after: &str) -> Vec<DiffRun> {
    let a: Vec<&str> = before.split_whitespace().collect();
    let b: Vec<&str> = after.split_whitespace().collect();
    if a.is_empty() && b.is_empty() {
        return Vec::new();
    }

    // table[i][j] = length of the longest common subsequence of a[..i], b[..j].
    let n = a.len();
    let m = b.len();
    let mut table = vec![vec![0u32; m + 1]; n + 1];
    for i in 0..n {
        for j in 0..m {
            table[i + 1][j + 1] = if a[i] == b[j] {
                table[i][j] + 1
            } else {
                table[i + 1][j].max(table[i][j + 1])
            };
        }
    }

    // Backtrack from the end, then reverse: the ops come out in reading order.
    let mut ops: Vec<(DiffKind, &str)> = Vec::new();
    let (mut i, mut j) = (n, m);
    while i > 0 || j > 0 {
        if i > 0 && j > 0 && a[i - 1] == b[j - 1] {
            ops.push((DiffKind::Equal, a[i - 1]));
            i -= 1;
            j -= 1;
        } else if i > 0 && (j == 0 || table[i - 1][j] >= table[i][j - 1]) {
            ops.push((DiffKind::Removed, a[i - 1]));
            i -= 1;
        } else {
            ops.push((DiffKind::Added, b[j - 1]));
            j -= 1;
        }
    }
    ops.reverse();

    // Merge adjacent ops of the same kind into one run.
    let mut runs: Vec<DiffRun> = Vec::new();
    for (kind, word) in ops {
        if let Some(last) = runs.last_mut() {
            if last.kind == kind {
                last.text.push(' ');
                last.text.push_str(word);
                continue;
            }
        }
        runs.push(DiffRun {
            kind,
            text: word.to_string(),
        });
    }

    // A replacement comes out of the backtrack as "added then removed"; put
    // the old word before the new one so it reads as a replacement. The pair
    // is adjacent, so the swap changes only the presentation, not the result
    // of applying the diff.
    let mut i = 0;
    while i + 1 < runs.len() {
        if runs[i].kind == DiffKind::Added && runs[i + 1].kind == DiffKind::Removed {
            runs.swap(i, i + 1);
        }
        i += 1;
    }
    runs
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds(runs: &[DiffRun]) -> Vec<DiffKind> {
        runs.iter().map(|r| r.kind).collect()
    }

    #[test]
    fn identical_texts_are_one_equal_run() {
        let runs = word_diff("שלום עולם", "שלום עולם");
        assert_eq!(kinds(&runs), vec![DiffKind::Equal]);
        assert_eq!(runs[0].text, "שלום עולם");
    }

    #[test]
    fn a_replaced_word_is_removed_then_added() {
        let runs = word_diff("שלום עולם", "שלום חברים");
        assert_eq!(
            kinds(&runs),
            vec![DiffKind::Equal, DiffKind::Removed, DiffKind::Added]
        );
        assert_eq!(runs[1].text, "עולם");
        assert_eq!(runs[2].text, "חברים");
    }

    #[test]
    fn an_inserted_word_keeps_neighbours_equal() {
        let runs = word_diff("אני הולך הביתה", "אני ממש הולך הביתה");
        assert_eq!(
            kinds(&runs),
            vec![DiffKind::Equal, DiffKind::Added, DiffKind::Equal]
        );
    }

    #[test]
    fn a_deleted_word_keeps_neighbours_equal() {
        let runs = word_diff("אני ממש הולך", "אני הולך");
        assert_eq!(
            kinds(&runs),
            vec![DiffKind::Equal, DiffKind::Removed, DiffKind::Equal]
        );
    }

    #[test]
    fn empty_to_text_is_one_added_run() {
        let runs = word_diff("", "שלום");
        assert_eq!(kinds(&runs), vec![DiffKind::Added]);
        assert_eq!(runs[0].text, "שלום");
    }

    #[test]
    fn text_to_empty_is_one_removed_run() {
        let runs = word_diff("שלום", "");
        assert_eq!(kinds(&runs), vec![DiffKind::Removed]);
        assert_eq!(runs[0].text, "שלום");
    }

    #[test]
    fn repeated_words_align_one_to_one() {
        let runs = word_diff("אחד שתיים אחד", "אחד שלוש אחד");
        assert_eq!(
            kinds(&runs),
            vec![
                DiffKind::Equal,
                DiffKind::Removed,
                DiffKind::Added,
                DiffKind::Equal
            ]
        );
        assert_eq!(runs[1].text, "שתיים");
        assert_eq!(runs[2].text, "שלוש");
    }

    #[test]
    fn mixed_latin_runs_stay_intact() {
        let runs = word_diff("גרסה 1.0 של C#", "גרסה 2.0 של C#");
        assert_eq!(
            kinds(&runs),
            vec![
                DiffKind::Equal,
                DiffKind::Removed,
                DiffKind::Added,
                DiffKind::Equal
            ]
        );
        assert_eq!(runs[1].text, "1.0");
        assert_eq!(runs[2].text, "2.0");
    }
}
