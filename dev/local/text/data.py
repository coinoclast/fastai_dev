#AUTOGENERATED! DO NOT EDIT! File to edit: dev/31_text_data.ipynb (unless otherwise specified).

__all__ = ['make_vocab', 'TensorText', 'Numericalize', 'LMDataLoader', 'pad_collate', 'SortedDL']

#Cell
from ..torch_basics import *
from ..test import *
from ..core import *
from ..data.all import *
from .core import *
from ..notebook.showdoc import show_doc

#Cell
def make_vocab(count, min_freq=3, max_vocab=60000):
    "Create a vocab of `max_vocab` size from `Counter` `count` with items present more than `min_freq`"
    vocab = [o for o,c in count.most_common(max_vocab) if c >= min_freq]
    for o in reversed(defaults.text_spec_tok): #Make sure all special tokens are in the vocab
        if o in vocab: vocab.remove(o)
        vocab.insert(0, o)
    vocab = vocab[:max_vocab]
    return vocab + [f'xxfake' for i in range(0, 8-len(vocab)%8)]

#Cell
class TensorText(TensorBase):
    def get_ctxs(self, max_n=10, **kwargs):
        n_samples = min(self.shape[0], max_n)
        df = pd.DataFrame(index = range(n_samples))
        return [df.iloc[i] for i in range(n_samples)]

    def display(self, ctxs): display_df(pd.DataFrame(ctxs))

#Cell
class Numericalize(Transform):
    "Reversible transform of tokenized texts to numericalized ids"
    def __init__(self, vocab=None, min_freq=3, max_vocab=60000, sep=' '):
        self.vocab,self.min_freq,self.max_vocab,self.sep = vocab,min_freq,max_vocab,sep
        self.o2i = None if vocab is None else defaultdict(int, {v:k for k,v in enumerate(vocab)})

    def setup(self, dsrc):
        if dsrc is None: return
        if self.vocab is None:
            dsrc = getattr(dsrc,'train',dsrc)
            count = Counter(p for o in dsrc for p in o)
            self.vocab = make_vocab(count, min_freq=self.min_freq, max_vocab=self.max_vocab)
            self.o2i = defaultdict(int, {v:k for k,v in enumerate(self.vocab) if v != 'xxfake'})

    def encodes(self, o): return TensorText(tensor([self.o2i  [o_] for o_ in o]))
    def decodes(self, o): return Str(self.sep.join([self.vocab[o_] for o_ in o if self.vocab[o_] != PAD]))

#Cell
#TODO: add backward
@delegates()
class LMDataLoader(TfmdDL):
    def __init__(self, dataset, lens=None, cache=2, bs=64, seq_len=72, num_workers=0, **kwargs):
        super().__init__(dataset=dataset, bs=bs, num_workers=num_workers, **kwargs)
        self.items = ReindexCollection([(o[0] if isinstance(o, tuple) else o) for o in dataset], cache=cache)
        self.seq_len = seq_len
        if lens is None: lens = [len(o) for o in self.items]
        self.lens = ReindexCollection(lens, idxs=self.items.idxs)
        # The "-1" is to allow for final label
        self.m = round_multiple(sum(lens)-1, bs*seq_len, round_down=True)
        self.n = self.m//(self.seq_len)
        self.spb = self.n//bs
        self.chunkify()

    def chunkify(self): self.chunks = Chunks(self.items, self.lens)
    def shuffle_fn(self,idxs):
        self.items.shuffle()
        self.chunkify()
        return idxs

    def create_item(self, seq):
        if seq>=self.n: raise IndexError
        st = ((seq%self.bs)*self.spb + (seq//self.bs)) * self.seq_len
        txt = self.chunks[st : st+self.seq_len+1]
        return txt[:-1],txt[1:]

    @classmethod
    def dbunchify(cls, dsrc, lens=None, bs=16, val_bs=None, shuffle_train=True, after_batch=None, **kwargs):
        n = len(dsrc.filts)-1
        bss = [bs] + [2*bs]*n if val_bs is None else [bs] + [val_bs]*n
        shuffles = [shuffle_train] + [False]*n
        if after_batch is None: after_batch = Cuda()
        lens = [None]*dsrc.n_subsets if lens is None else [L(lens, use_list=None)[f] for f in dsrc.filts]
        return DataBunch(*[cls(dsrc.subset(i), lens=l, bs=b, shuffle=s, drop_last=s, after_batch=after_batch, **kwargs)
                           for i,(b,s,l) in enumerate(zip(bss, shuffles, lens))])

#Cell
def pad_collate(samples, pad_idx=1, pad_first=False, backwards=False):
    "Function that collect samples and adds padding. Flips token order if needed"
    max_len = max([len(s[0]) for s in samples])
    res = torch.zeros(len(samples), max_len).long() + pad_idx
    if backwards: pad_first = not pad_first
    for i,s in enumerate(samples):
        sl = slice(-len(s[0]), sys.maxsize) if pad_first else slice(0, len(s[0]))
        res[i,sl] = LongTensor(s[0])
    if backwards: res = res.flip(1)
    return res, tensor(np.array([s[1] for s in samples]))

#Cell
def _default_sort(x): return len(x[0])

@delegates(TfmdDL)
class SortedDL(TfmdDL):
    def __init__(self, dataset, sort_func=None, **kwargs):
        super().__init__(dataset, **kwargs)
        self.sort_func = _default_sort if sort_func is None else sort_func

    def get_idxs(self):
        idxs = super().get_idxs()
        if self.shuffle: return idxs
        return sorted(idxs, key=lambda i: self.sort_func(self.do_item(i)), reverse=True)

    def shuffle_fn(self,idxs):
        idxs = np.random.permutation(len(self.dataset))
        sz = self.bs*50
        chunks = [idxs[i:i+sz] for i in range(0, len(idxs), sz)]
        chunks = [sorted(s, key=lambda i: self.sort_func(self.do_item(i)), reverse=True) for s in chunks]
        max_ck = np.argmax([self.sort_func(self.do_item(ck[0])) for ck in chunks])  # find the chunk with the largest key,
        chunks[0][0],chunks[max_ck][0] = chunks[max_ck][0],chunks[0][0]     # then make sure it goes first.
        sort_idx = np.concatenate(chunks)

        sz = self.bs
        batches = [sort_idx[i:i+sz] for i in range(0, len(sort_idx), sz)]
        sort_idx = np.concatenate(np.random.permutation(batches[1:-1])) if len(batches) > 2 else np.array([],dtype=np.int)
        sort_idx = np.concatenate((batches[0], sort_idx) if len(batches)==1 else (batches[0], sort_idx,batches[-1]))
        return iter(sort_idx)