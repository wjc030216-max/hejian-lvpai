"""Phase-two character service. Task state is owned by TaskJournal."""
import logging
from flask import Flask
from common import portrait_provider as provider
from common.durable_generation import install
from common.service_app import protect,serve
from common import service_log
provider.app=Flask(__name__)
provider.logger=logging.getLogger(__name__)
app=provider.app
service_log.install("charactergen")
protect(app,{'local-phase2'},public={'/api/v1/image/health'})
worker=install(provider,'character')
if __name__=='__main__': serve(app,10005)
