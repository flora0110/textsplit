import numpy as np
from numpy.linalg import norm
import random

def split_greedy(docmat, min_gain=None, max_splits=None):
    """
    Iteratively segment a document into segments being greedy about the
    next choice. This gives very accurate results on crafted documents, i.e.
    artificial concatenations of random documents.

    `min_gain` is the minimum quantity a split has to improve the score to be
    made. If not given `total` is not computed.
    `max_splits` is a limit on the number of splits.
    Either `min_gain` or `max_splits` have to be given.

    Whenever the iteration reaches the while block the following holds:
    `cuts` == splits + [L] where splits are the segment start indices
    `segscore` maps all segment start indices to segment vector lengths
    `score_l[i]` is the cumulated vector length from the cut left of i to i
    `score_r[i]` is the cumulated vector length from i to the cut right of i
    `score_out[i]` is the sum of all segscores not including the segment at i
    `scores[i]` is the sum of all segment vector lengths if we split at i

    These quantities are repaired after determining a next split from `scores`.

    Returns `total`, `splits`, `gains` where
    - `total` is the score diminished by len(splits) * min_gain to make it
      continuous in the input. It is comparable to the output of split_optimal.
    - `splits` is the list of splits
    - `gains` is a list of uplift each split contributes vs. leaving it out

    Note: The splitting strategy suggests all resulting splits will have gain at
    least `min_gain`. This is not the case as new splits can decrease the gain
    of others. This can be repaired by blocking positions where a split would
    decreas the gain of an existing one to less than `min_gain` but is not
    impemented here.
    """
    L, dim = docmat.shape

    assert max_splits is not None or (min_gain is not None and min_gain > 0)

    # norm(cumvecs[j] - cumvecs[i]) == norm(w_i + ... + w_{j-1})
    cumvecs = np.cumsum(np.vstack((np.zeros((1, dim)), docmat)), axis=0)

    # cut[0] seg[0] cut[1] seg[1] ... seg[L-1] cut[L]
    cuts = [0, L]
    segscore = dict()
    segscore[0] = norm(cumvecs[L, :] - cumvecs[0, :], ord=2)
    segscore[L] = 0     # corner case, always 0
    score_l = norm(cumvecs[:L, :] - cumvecs[0, :], axis=1, ord=2)
    score_r = norm(cumvecs[L, :] - cumvecs[:L, :], axis=1, ord=2)
    score_out = np.zeros(L)
    score_out[0] = -np.inf # forbidden split position
    score = score_out + score_l + score_r

    while True:
        split = np.argmax(score)

        if score[split] == - np.inf:
            break

        cut_l = max([c for c in cuts if c < split])
        cut_r = min([c for c in cuts if split < c])
        split_gain = score_l[split] + score_r[split] - segscore[cut_l]

        if min_gain is not None:
            if split_gain < min_gain:
                break

        segscore[cut_l] = score_l[split]
        segscore[split] = score_r[split]

        cuts.append(split)
        cuts = sorted(cuts)

        if max_splits is not None:
            if len(cuts) >= max_splits + 2:
                break

        # differential changes to score arrays
        score_l[split:cut_r] = norm(
            cumvecs[split:cut_r, :] - cumvecs[split, :], axis=1, ord=2)
        score_r[cut_l:split] = norm(
            cumvecs[split, :] - cumvecs[cut_l:split, :], axis=1, ord=2)

        # adding following constant not necessary, only for score semantics
        score_out += split_gain
        score_out[cut_l:split] += segscore[split] - split_gain
        score_out[split:cut_r] += segscore[cut_l] - split_gain
        score_out[split] = -np.inf

        # update score
        score = score_out + score_l + score_r

    cuts = sorted(cuts)
    splits = cuts[1:-1]
    if min_gain is None:
        total = None
    else:
        total = sum(norm(cumvecs[l, :] - cumvecs[r, :], ord=2)
            for l, r in zip(cuts[:-1], cuts[1:])) - len(splits) * min_gain
    gains = []
    for beg, cen, end in zip(cuts[:-2], cuts[1:-1], cuts[2:]):
        no_split_score = norm(cumvecs[end, :] - cumvecs[beg, :], ord=2)
        gains.append(segscore[beg] + segscore[cen] - no_split_score)

    return total, splits, gains


def split_optimal(docmat, penalty, seg_limit=None):
    """
    Determine the configuration of splits with the highest score, given that
    splitting has a cost of `penalty`. `seg_limit` is a limitation on the length
    of a segment that saves memory and computation, but gives poor results
    should there be no split withing the range.
    The algorithm is built upon the idea that there is a accumulated score
    matrix containing the maximal score of creating a segment (i, j), containing
    all words [w_i, ..., w_j] at position i, j. The matrix `acc` is indexed to
    contain the first `seg_limit` elements of each row of the score matrix.
    `colmax` contains the column maxima of the score matrix.
    `ptr` is a backtracking pointer to determine the splits made while
    forward accumulating the highest score in the score matrix.
    """
    L, dim = docmat.shape
    lim = L if seg_limit is None else seg_limit
    assert lim > 0
    assert penalty > 0

    acc = np.full((L,lim), -np.inf, dtype=np.float32)
    colmax = np.full((L,), -np.inf, dtype=np.float32)
    ptr = np.zeros(L, dtype=np.int32)

    for i in range(L):
        score_so_far = colmax[i-1] if i > 0 else 0.

        ctxvecs = np.cumsum(docmat[i:i+lim, :], axis=0)
        winsz = ctxvecs.shape[0]
        score = norm(ctxvecs, axis=1, ord=2)
        acc[i, :winsz] = score_so_far - penalty + score

        deltas = np.where(acc[i, :winsz] > colmax[i:i+lim])[0]
        js = i + deltas
        colmax[js] = acc[i, deltas]
        ptr[js] = i

    path = [ptr[-1]]
    while path[0] != 0:
        path.insert(0, ptr[path[0] - 1])

    splits = path[1:]
    gains = get_gains(docmat, path[1:])
    optimal = all(np.diff([0] + splits + [L]) < lim)

    total = colmax[-1] + penalty

    if seg_limit is not None:
        return total, splits, gains, optimal
    else:
        return total, splits, gains


def get_penalty(docmats, segment_len):
    """
    Determine penalty for segments having length `segment_len` on average.
    This is achieved by stochastically rounding the expected number
    of splits per document `max_splits` and averaging the two lowest gains for
    splitting the document `max_splits` + 1 times.
    """
    penalties = []
    for docmat in docmats:
        avg_n_seg = docmat.shape[0] / segment_len
        max_splits = int(avg_n_seg) + (random.random() < avg_n_seg % 1) - 1
        if max_splits >= 1:
            _, _, nextgains = split_greedy(docmat, max_splits=max_splits + 1)
            low_gains = sorted(nextgains)[:2]
            penalties.append(1/2 * low_gains[0] + 1/2 * low_gains[1])
    return np.mean(penalties)


def P_k(splits_ref, splits_hyp, N):
    """
    Metric to evaluate reference splits against hypothesised splits.
    Lower is better.
    `N` is the text length.
    """
    k = round(N / (len(splits_ref) + 1) / 2 - 1)
    ref = np.array(splits_ref, dtype=np.int32)
    hyp = np.array(splits_hyp, dtype=np.int32)

    def is_split_between(splits, l, r):
        return np.sometrue(np.logical_and(splits - l > 0, splits - r < 0))

    acc = 0
    for i in range(N-k):
        acc += is_split_between(ref,i,i+k) != is_split_between(hyp,i,i+k)

    return acc / (N-k)


def get_total(docmat, splits, penalty):
    """
    Compute the total score of a split configuration with given penalty.
    """
    L, dim = docmat.shape
    cuts = [0] + list(splits) + [L]
    cumvecs = np.cumsum(np.vstack((np.zeros((1, dim)), docmat)), axis=0)
    return sum(norm(cumvecs[l, :] - cumvecs[r, :], ord=2)
        for l, r in zip(cuts[:-1], cuts[1:])) - len(splits) * penalty


def get_gains(docmat, splits, width=None):
    """
    Calculate gains of the splits towards the left and right neighbouring
    split.
    If `width` is given, calculate gains of the splits towards a centered window
    of length 2 * `width`.
    """
    gains = []
    L = docmat.shape[0]
    for beg, cen, end in zip([0] + splits[:-1], splits, splits[1:] + [L]):
        if width is not None and width > 0:
            beg, end = max(cen - width, 0), min(cen + width, L)

        slice_l, slice_r, slice_t = [slice(beg, cen),  # left context
                                     slice(cen, end),  # right context
                                     slice(beg, end)]  # total context

        gains.append([norm(docmat[slice_l, :].sum(axis=0), ord=2)
                    + norm(docmat[slice_r, :].sum(axis=0), ord=2)
                    - norm(docmat[slice_t, :].sum(axis=0), ord=2)])
    return gains
