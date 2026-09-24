"""Fala/VAD: limites, ruído, sílabas e cache, sem provedor nem render."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
import segmentacao_fala as sf

class SegmentationTests(unittest.TestCase):
    def plan(self, low, high=None, words=(), ranges=None, duration=20):
        return sf.planejar_segmentos(duration,ranges or [[0,duration]],low,low if high is None else high,words)
    def covers(self, segments,a,b):
        return any(x<=a+1e-7 and y>=b-1e-7 for x,y in segments)
    def test_stretched_word_does_not_keep_ten_seconds_of_machine_noise(self):
        result=self.plan([[2,3],[13,14]],words=[dict(t=2,e=14,w='fazendo')])
        self.assertTrue(self.covers(result,2,3));self.assertTrue(self.covers(result,13,14))
        self.assertFalse(any(b>4 and a<12 for a,b in result))
    def test_vad_retains_speech_missing_from_whisper(self):
        result=self.plan([[1,1.4],[4,4.4]],words=[dict(t=1,e=1.4,w='início')])
        self.assertTrue(self.covers(result,4,4.4))
    def test_short_low_voice_asr_disagreement_is_preserved(self):
        result=self.plan([],words=[dict(t=1,e=1.18,w='sim')])
        self.assertTrue(self.covers(result,1,1.18))
    def test_detector_disagreement_never_removes_detected_voice(self):
        result=self.plan([[1,2]],[[1,2],[3,3.2]])
        self.assertTrue(self.covers(result,3,3.2))
    def test_brief_unvoiced_consonant_inside_word_never_creates_cut(self):
        result=self.plan([[1,1.2],[1.31,1.5]],words=[dict(t=1,e=1.5,w='próprio')])
        self.assertEqual(len(result),1);self.assertTrue(self.covers(result,1,1.5))
    def test_approved_exclusion_allows_only_one_boundary_frame_not_padding(self):
        result=self.plan([[0,3]],ranges=[[0,1.03],[1.16,3]])
        self.assertEqual(len(result),2)
        self.assertLess(result[0][1]-1.03,1/30);self.assertLess(1.16-result[1][0],1/30)
        self.assertLess(result[0][1],result[1][0])
    def test_outward_frame_rounding_preserves_both_acoustic_edges(self):
        result=self.plan([[1.019,2.017]])
        self.assertLessEqual(result[0][0],1.019-1/30)
        self.assertGreaterEqual(result[0][1],2.017+1/30)
        for a,b in result:
            self.assertAlmostEqual(a*30,round(a*30));self.assertAlmostEqual(b*30,round(b*30))
    def test_retained_word_at_491_22_is_not_trimmed_to_next_frame(self):
        result=self.plan([[491.22,492.4]],ranges=[[491.22,493]],duration=500)
        self.assertTrue(self.covers(result,491.22,492.4))
        self.assertAlmostEqual(result[0][0],491.2)
    def test_outward_ranges_never_duplicate_overlapping_audio_frames(self):
        result=self.plan([[0,3]],ranges=[[0,1.01],[1.02,3]])
        self.assertEqual(result,[[0,3]])
    def test_last_outward_frame_is_clipped_to_real_audio_duration(self):
        result=self.plan([[0,2.015]],ranges=[[0,2.015]],duration=2.015)
        self.assertEqual(result,[[0,2.015]])
    def test_no_voice_returns_empty_instead_of_an_arbitrary_full_take(self):
        self.assertEqual(self.plan([]),[])
    def test_loud_synthetic_drill_is_not_a_volume_based_speech_island(self):
        rate=16000;t=np.arange(rate*4,dtype=np.float32)/rate
        noise=np.random.default_rng(101).normal(0,.08,len(t)).astype(np.float32)
        audio=(.16*np.sin(2*np.pi*600*t)+.1*np.sin(2*np.pi*1700*t)+noise).astype(np.float32)
        low,high=sf.detectar_fala(audio)
        self.assertEqual(sf.planejar_segmentos(4,[[0,4]],low,high),[])
    def test_invalid_or_overaggressive_parameters_are_rejected(self):
        with self.assertRaises(ValueError):sf.planejar_segmentos(2,[[0,2]],[],[],pausa_min=.01)
        with self.assertRaises(ValueError):sf.planejar_segmentos(float('nan'),[[0,2]],[],[])

class AcousticGuardTests(unittest.TestCase):
    def test_overlapping_word_guards_only_80ms_before_real_voice(self):
        word=dict(w='eles',t=491.7,e=492.46)
        guards,uncertain=sf.protecoes_palavras([word],[[492.288,493]])
        self.assertEqual(uncertain,[])
        self.assertAlmostEqual(guards[0][0],492.208)
        self.assertAlmostEqual(guards[0][1],492.46)
        self.assertFalse(any(a<=491.7<=b for a,b in guards))
    def test_quiet_word_entirely_outside_vad_is_kept_and_reported(self):
        word=dict(w='e',t=257.06,e=257.66)
        guards,uncertain=sf.protecoes_palavras([word],[[258.272,260]])
        self.assertEqual(guards,[[257.06,257.66]])
        self.assertEqual(uncertain[0]['w'],'e')
        report={}
        segments=sf.planejar_segmentos(270,[[256,261]],[[258.272,260]],[],[word],relatorio=report)
        self.assertTrue(any(a<=257.06 and b>=257.66 for a,b in segments))
        self.assertEqual(report['palavras_sem_vad'][0]['w'],'e')
    def test_eighty_ms_consonant_margin_is_not_cut_at_acoustic_boundary(self):
        word=dict(w='primeiro',t=416.6,e=417.44)
        segments=sf.planejar_segmentos(420,[[416,419]],[[417.28,418]],[],[word])
        self.assertTrue(any(a<=417.2 and b>=418 for a,b in segments))
        self.assertGreater(segments[0][0],416.6)
    def test_internal_pause_up_to_160ms_stays_continuous(self):
        word=dict(w='proprio',t=1,e=1.6)
        segments=sf.planejar_segmentos(3,[[0,3]],[[1,1.2],[1.36,1.6]],[],[word])
        self.assertEqual(len(segments),1)
        self.assertLessEqual(segments[0][0],1);self.assertGreaterEqual(segments[0][1],1.6)
    def test_long_pause_inside_short_asr_word_is_not_restored(self):
        word=dict(w='como',t=1,e=1.95)
        segments=sf.planejar_segmentos(3,[[0,3]],[[1,1.1],[1.9,1.95]],[],[word])
        self.assertEqual(len(segments),2)
        self.assertLess(segments[0][1],segments[1][0])
    def test_weak_refined_ghost_e_does_not_recreate_leading_pause(self):
        original=[dict(w='E',t=553.54,e=554.12)]
        refined=[dict(w='e',t=553.14,e=553.68,probabilidade=.5478)]
        words=sf.combinar_palavras(original,refined)
        segments=sf.planejar_segmentos(560,[[550,555]],[[554.08,555]],[],words)
        self.assertGreaterEqual(segments[0][0],553.9)
        self.assertFalse(any(a<=553.14<=b for a,b in segments))
    def test_weak_refinement_overlapping_voice_keeps_eighty_ms_margin(self):
        words=sf.combinar_palavras([],[dict(w='assim',t=229.8,e=230.2,probabilidade=.11)])
        guards,uncertain=sf.protecoes_palavras(words,[[230.112,231]])
        self.assertAlmostEqual(guards[0][0],230.032)
        self.assertEqual(uncertain,[])
    def test_weak_original_outside_vad_stays_despite_refinement_filter(self):
        original=[dict(w='e',t=257.06,e=257.66,probabilidade=.1)]
        words=sf.combinar_palavras(original,[])
        guards,uncertain=sf.protecoes_palavras(words,[[258.272,260]])
        self.assertEqual(guards,[[257.06,257.66]])
        self.assertEqual(len(uncertain),1)
    def test_uncertainty_report_only_includes_approved_source_ranges(self):
        words=[dict(w='fica',t=1,e=1.3),dict(w='removida',t=4,e=4.3)]
        report={}
        sf.planejar_segmentos(5,[[0,2]],[],[],words,relatorio=report)
        self.assertEqual([p['w'] for p in report['palavras_sem_vad']],['fica'])

class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.wav=Path(self.temp.name)/'voz16k.wav'
        with wave.open(str(self.wav),'wb') as w:w.setparams((1,2,16000,0,'NONE','none'));w.writeframes(b'\0\0'*16000*12)
        self.original=self.wav.read_bytes();self.audio=np.zeros(16000*12,dtype=np.float32)
        self.words=[dict(w='como',t=1,e=2.5),dict(w='fazendo',t=8,e=9.5)]
        self.model={'nome':'test','algoritmo':'v1'};self.calls=[]
    def transcribe(self,audio,a,b):
        self.calls.append((a,b));return [dict(w='fala',t=a+.1,e=a+.4)]
    def refine(self,**kwargs):
        return sf.refinar_palavras(self.wav,self.words,self.audio,[],[],transcrever=self.transcribe,
            modelo_info=self.model,log=lambda _:None,**kwargs)
    def test_completed_windows_are_cached_without_touching_original(self):
        self.refine();self.assertEqual(len(self.calls),2);self.refine();self.assertEqual(len(self.calls),2)
        self.assertEqual(self.wav.read_bytes(),self.original)
        cache=json.loads(self.wav.with_name('whisper_refino_cortes.json').read_text())
        self.assertEqual(len(cache['janelas']),2);self.assertEqual(cache['modelo'],self.model)
    def test_interrupted_window_resumes_only_the_missing_window(self):
        normal=self.transcribe
        def fail_second(audio,a,b):
            if a>5:raise RuntimeError('simulated interruption')
            return normal(audio,a,b)
        with self.assertRaises(RuntimeError):
            sf.refinar_palavras(self.wav,self.words,self.audio,[],[],transcrever=fail_second,modelo_info=self.model,log=lambda _:None)
        self.assertEqual(len(self.calls),1)
        self.refine();self.assertEqual(len(self.calls),2)
    def test_audio_transcript_and_model_each_invalidate_cache(self):
        self.refine()
        st=self.wav.stat();os.utime(self.wav,ns=(st.st_atime_ns,st.st_mtime_ns+1000000));self.refine()
        self.assertEqual(len(self.calls),4)
        self.words[0]['w']='corrigido';self.refine();self.assertEqual(len(self.calls),6)
        self.model['algoritmo']='v2';self.refine();self.assertEqual(len(self.calls),8)
    def test_missing_whisper_speech_creates_a_contextual_refinement_window(self):
        windows=sf.janelas_refino(20,[],[[6,7]],[[6,7]])
        self.assertEqual(windows,[[4.5,8.5]])
    def test_large_refinement_windows_are_bounded_with_context_overlap(self):
        windows=sf.janelas_refino(150,[dict(t=1,e=148)],[],[])
        self.assertTrue(all(b-a<=45 for a,b in windows))
        self.assertEqual(windows[0][0],0)
        self.assertGreater(windows[0][1],windows[1][0])
    def test_refined_short_words_protect_a_voice_onset_missing_from_detector(self):
        words=[dict(w='E',t=381.98,e=382.52)]
        segs=sf.planejar_segmentos(400,[[380,390]],[[382.56,388.8]],[[382.68,388.6]],words)
        self.assertTrue(any(a<=381.98 and b>=388.8 for a,b in segs))


class SafeAlignmentTests(unittest.TestCase):
    def word(self,text,a,b,score=None):
        p=dict(w=text,t=a,e=b)
        if score is not None:p['probabilidade']=score
        return p
    def test_confident_identical_overlapping_word_replaces_old_padding(self):
        old=self.word('Treino,',1,1.7);new=self.word('treino',1.55,1.72,.99)
        anchor=self.word('corpo',1.7,2.1)
        result=sf.combinar_palavras([old,anchor],[new,dict(anchor,probabilidade=.99)])
        self.assertNotIn(old,result);self.assertIn(new,result)
    def test_low_confidence_refinement_keeps_original_voice_guard(self):
        old=self.word('treino',1,1.7);new=self.word('treino',1.55,1.72,.59)
        self.assertIn(old,sf.combinar_palavras([old],[new]))
    def test_different_word_never_replaces_the_original(self):
        old=self.word('treino',1,1.7);new=self.word('ruido',1.55,1.72,.99)
        self.assertIn(old,sf.combinar_palavras([old],[new]))
    def test_same_word_in_another_take_never_replaces_current_take(self):
        old=self.word('muito',359.86,360.3);new=self.word('muito',364.1,364.56,.9971)
        self.assertIn(old,sf.combinar_palavras([old],[new]))
    def test_two_plausible_refined_occurrences_are_ambiguous(self):
        old=self.word('treino',1,1.8)
        new=[self.word('treino',1.1,1.3,.99),self.word('treino',1.5,1.7,.99)]
        self.assertIn(old,sf.combinar_palavras([old],new))
    def test_one_refined_occurrence_cannot_replace_two_originals(self):
        old=[self.word('treino',1,1.6),self.word('treino',1.4,1.9)]
        new=self.word('treino',1.45,1.55,.99)
        result=sf.combinar_palavras(old,[new])
        self.assertTrue(all(p in result for p in old))
    def test_single_letter_requires_a_matching_neighbor(self):
        old=self.word('E',1,1.7);new=self.word('e',1.55,1.7,.99)
        self.assertIn(old,sf.combinar_palavras([old],[new]))
        neighbor=self.word('treino',1.7,2.1)
        result=sf.combinar_palavras([old,neighbor],[new,dict(neighbor,probabilidade=.99)])
        self.assertNotIn(old,result);self.assertIn(new,result)
    def test_vad_keeps_quiet_earlier_speech_even_after_confident_alignment(self):
        old=self.word('treino',1,1.7);new=self.word('treino',1.55,1.72,.99)
        anchor=self.word('corpo',1.7,2.1)
        combined=sf.combinar_palavras([old,anchor],[new,dict(anchor,probabilidade=.99)])
        self.assertNotIn(old,combined)
        segments=sf.planejar_segmentos(3,[[0,3]],[[1,1.72]],[],combined)
        self.assertLessEqual(segments[0][0],1);self.assertGreaterEqual(segments[0][1],1.72)
    def test_duplicate_overlap_window_does_not_create_false_ambiguity(self):
        old=self.word('treino',1,1.7);new=self.word('treino',1.55,1.72,.99)
        anchor=self.word('corpo',1.7,2.1)
        result=sf.combinar_palavras([old,anchor],[new,dict(new),dict(anchor,probabilidade=.99)])
        self.assertNotIn(old,result);self.assertEqual(sum(p==new for p in result),1)
    def test_every_word_requires_a_neighbor_anchor(self):
        old=self.word('treino',1,1.7);new=self.word('treino',1.55,1.72,.99)
        self.assertIn(old,sf.combinar_palavras([old],[new]))
    def test_confidence_must_be_explicit_numeric_finite_and_valid(self):
        old=self.word('treino',1,1.7);anchor=self.word('corpo',1.7,2.1)
        for value in (None,float('nan'),float('inf'),float('-inf'),'0.99',True,False,-.1,1.1):
            with self.subTest(probabilidade=value):
                new=self.word('treino',1.55,1.72)
                if value is not None:new['probabilidade']=value
                result=sf.combinar_palavras([old,anchor],[new,dict(anchor,probabilidade=.99)])
                self.assertIn(old,result)
    def test_textual_neighbor_from_another_time_is_not_an_anchor(self):
        old=self.word('treino',1,1.7);anchor=self.word('corpo',1.7,2.1)
        refined=[self.word('treino',1.55,1.72,.99),self.word('corpo',50,50.4,.99)]
        self.assertIn(old,sf.combinar_palavras([old,anchor],refined))
    def test_anchor_itself_requires_explicit_reliable_confidence(self):
        old=self.word('treino',1,1.7);anchor=self.word('corpo',1.7,2.1)
        for value in (None,.59,float('nan')):
            neighbor=dict(anchor)
            if value is not None:neighbor['probabilidade']=value
            refined=[self.word('treino',1.55,1.72,.99),neighbor]
            self.assertIn(old,sf.combinar_palavras([old,anchor],refined))
    def test_invalid_duplicate_confidence_does_not_override_valid_alignment(self):
        old=self.word('treino',1,1.7);anchor=self.word('corpo',1.7,2.1)
        good=self.word('treino',1.55,1.72,.99);bad=dict(good,probabilidade=float('nan'))
        for duplicates in ([good,bad],[bad,good]):
            result=sf.combinar_palavras([old,anchor],duplicates+[dict(anchor,probabilidade=.99)])
            self.assertNotIn(old,result);self.assertIn(good,result)
    def test_short_original_inside_confirmed_long_pause_gets_refinement(self):
        original=[self.word('treino',4,4.7)]
        self.assertEqual(sf.janelas_refino(10,original,[[4.6,4.7]],[[4.6,4.7]]),[[2.5,6.2]])
    def test_sub_half_second_vad_disagreement_does_not_trigger_new_refinement(self):
        original=[self.word('E',3.6,4),self.word('treino',4,4.5)]
        voice=[[3.6,4],[4.45,4.5]]
        self.assertEqual(sf.janelas_refino(5,original,voice,voice),[])

if __name__=='__main__':unittest.main()
