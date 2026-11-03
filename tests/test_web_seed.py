"""Seed resolution belongs to accepted web runs, never to the genetic operators."""
from html.parser import HTMLParser
import json
import random
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from scheduler_web.app import create_app
from scheduler_web.services import AUTO_SEED_UPPER_BOUND
from test_web_ui import install_fixture


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name)
        self.manager = self.app.state.manager
        self.dataset = install_fixture(self.manager)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {'x-local-token': self.app.state.token}
        self.settings = {'population_size':6,'elite_count':1,'max_generations':0}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def start(self, **body):
        response = self.client.post('/api/runs',headers=self.headers,
            json={'dataset_id':self.dataset,'settings':self.settings,**body})
        if response.status_code == 200:
            self.manager.worker.join(15)
        return response

    def test_ui_defaults_to_auto_without_fixed_seed(self):
        class Fields(HTMLParser):
            def __init__(self): super().__init__(); self.options=[]; self.manual=None
            def handle_starttag(self,tag,attrs):
                a=dict(attrs)
                if tag=='option' and a.get('value') in ('auto','manual'): self.options.append(a)
                if a.get('id')=='manualSeed': self.manual=a
        fields=Fields();fields.feed(self.client.get('/').text)
        self.assertIn('selected',next(o for o in fields.options if o['value']=='auto'))
        self.assertNotIn('selected',next(o for o in fields.options if o['value']=='manual'))
        self.assertIn('disabled',fields.manual);self.assertNotIn('value',fields.manual)

    def test_default_auto_seed_response_and_global_rng_unchanged(self):
        before=random.getstate()
        with patch('scheduler_web.services.secrets.randbelow',return_value=4294967295) as generate:
            r=self.start().json()
        generate.assert_called_once_with(AUTO_SEED_UPPER_BOUND)
        self.assertEqual(r['seed_mode'],'auto');self.assertEqual(type(r['settings']['random_seed']),int)
        self.assertEqual(r['settings']['random_seed'],4294967295)
        self.assertEqual(random.getstate(),before)

    def test_each_accepted_run_calls_generator_independently(self):
        with patch('scheduler_web.services.secrets.randbelow',side_effect=[12,34]) as generate:
            a=self.start(seed_mode='auto').json();b=self.start(seed_mode='auto').json()
        self.assertEqual(generate.call_count,2)
        self.assertEqual([a['settings']['random_seed'],b['settings']['random_seed']],[12,34])

    def test_automatic_draws_may_repeat_without_retry(self):
        with patch('scheduler_web.services.secrets.randbelow',return_value=0) as generate:
            a=self.start().json();b=self.start().json()
        self.assertEqual(generate.call_count,2)
        self.assertEqual(a['settings']['random_seed'],0);self.assertEqual(b['settings']['random_seed'],0)

    def test_manual_and_legacy_preserve_exact_integer(self):
        with patch('scheduler_web.services.secrets.randbelow') as generate:
            for mode in ('manual',None):
                kw={'seed_mode':mode} if mode else {}
                value=-(2**70)
                r=self.start(settings={**self.settings,'random_seed':value},**kw)
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.json()['settings']['random_seed'],value)
                self.assertEqual(r.json()['seed_mode'],'manual')
            generate.assert_not_called()

    def test_malformed_manual_values_rejected_without_generation(self):
        with patch('scheduler_web.services.secrets.randbelow') as generate:
            for value in (None,True,1.5,'11','',[]):
                with self.subTest(value=value):
                    self.assertEqual(self.start(seed_mode='manual',settings={**self.settings,'random_seed':value}).status_code,422)
            self.assertEqual(self.start(seed_mode='manual').status_code,422)
            self.assertEqual(self.start(seed_mode='unknown').status_code,422)
            self.assertEqual(self.start(seed_mode='auto',settings={**self.settings,'random_seed':11}).status_code,422)
            generate.assert_not_called()

    def test_rejected_settings_dataset_or_busy_run_do_not_consume_seed(self):
        with patch('scheduler_web.services.secrets.randbelow') as generate:
            self.assertEqual(self.start(settings={**self.settings,'population_size':1}).status_code,422)
            self.assertEqual(self.start(dataset_id='b'*32).status_code,422)
            self.manager.active_id='c'*32
            self.assertEqual(self.start().status_code,409)
            self.manager.active_id=None
            generate.assert_not_called()

    def test_resolved_seed_saved_in_metadata_report_and_history(self):
        with patch('scheduler_web.services.secrets.randbelow',return_value=987654):
            r=self.start(seed_mode='auto').json()
        directory=self.manager.path('runs',r['id'])
        for name in ('metadata.json','report.json'):
            content=json.loads((directory/name).read_text(encoding='utf-8'))
            self.assertEqual(content['settings']['random_seed'],987654)
            self.assertEqual(content['seed_mode'],'auto')
        history=self.client.get('/api/state').json()['runs'][0]
        self.assertEqual(history['settings']['random_seed'],987654)

    def test_auto_seed_reproduces_exact_genotypes_in_manual_mode(self):
        settings={**self.settings,'max_generations':3}
        with patch('scheduler_web.services.secrets.randbelow',return_value=1234567) as generate:
            a=self.start(settings=settings).json()
            b=self.start(seed_mode='manual',settings={**settings,'random_seed':a['settings']['random_seed']}).json()
        generate.assert_called_once()
        ar=self.manager.events(a['id']);br=self.manager.events(b['id'])
        self.assertEqual(ar['run']['status'],'Completed');self.assertEqual(br['run']['status'],'Completed')
        self.assertEqual([r['best_fingerprint'] for r in ar['events']],[r['best_fingerprint'] for r in br['events']])
        self.assertEqual(ar['run']['fitness_calls'],br['run']['fitness_calls'])
        self.assertEqual(self.manager.population(a['id'],3),self.manager.population(b['id'],3))

    def test_seed_generated_once_across_generations_and_cooperative_stop(self):
        from scheduler_web import services
        real=services.run_evolution
        def observed(context,settings,**kw):
            publish=kw['on_generation']
            def callback(progress):
                publish(progress)
                if progress.statistics.generation_index==1:self.manager.cancel.set()
            return real(context,settings,**{**kw,'on_generation':callback})
        with patch('scheduler_web.services.secrets.randbelow',return_value=91) as generate,patch('scheduler_web.services.run_evolution',observed):
            r=self.start(settings={**self.settings,'max_generations':10}).json()
        generate.assert_called_once()
        stored=self.manager.get_run(r['id'])
        self.assertEqual(stored['status'],'Stopped');self.assertEqual(stored['completed_generations'],1)
        self.assertEqual(stored['settings']['random_seed'],91)
