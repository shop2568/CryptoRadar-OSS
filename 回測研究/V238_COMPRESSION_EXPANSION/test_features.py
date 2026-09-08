import ast,inspect,unittest
import numpy as np
import pandas as pd
from features import calculate


class Tests(unittest.TestCase):
    def setUp(self):
        ix=pd.date_range('2024-01-01',periods=100,freq='15min',tz='UTC');p=100+np.arange(100)/100
        self.f=pd.DataFrame(dict(open=p,close=p+.2,high=p+.5,low=p-.5,volume=p),index=ix)
        self.r=dict(direction='LONG',signal_time=ix[70],entry_time=ix[71])

    def test_future(self):
        g=self.f.copy();g.loc[g.index>=self.r['entry_time']]*=99
        self.assertEqual(calculate(self.r,self.f),calculate(self.r,g))

    def test_incomplete(self):
        with self.assertRaisesRegex(ValueError,'INCOMPLETE_SIGNAL'):
            calculate(dict(self.r,entry_time=self.r['signal_time']),self.f)

    def test_missing(self):
        with self.assertRaisesRegex(ValueError,'SEQUENCE_DATA_MISSING'):
            calculate(self.r,self.f.drop(self.f.index[60]))

    def test_no_signal_fallback(self):
        with self.assertRaises(ValueError):calculate(dict(self.r,signal_time=pd.NaT),self.f)

    def test_outcome_independent(self):
        self.assertEqual(calculate(self.r,self.f),calculate(dict(self.r,pnl=999,outcome='WIN',symbol='FAKE'),self.f))

    def test_row_dependency_whitelist(self):
        tree=ast.parse(inspect.getsource(calculate))
        keys={n.slice.value for n in ast.walk(tree) if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Name) and n.value.id=='row' and isinstance(n.slice,ast.Constant)}
        self.assertEqual(keys,{'entry_time','signal_time','direction'})

    def test_zero_volume_no_default(self):
        g=self.f.copy();g.volume=0
        with self.assertRaisesRegex(ValueError,'VOLUME_REFERENCE_MISSING'):calculate(self.r,g)

    def test_mirror(self):
        a=calculate(self.r,self.f);b=calculate(dict(self.r,direction='SHORT'),self.f)
        self.assertAlmostEqual(a['signed_body_ratio'],-b['signed_body_ratio'])
        self.assertAlmostEqual(a['signed_clv']+b['signed_clv'],1)


if __name__=='__main__':unittest.main()
