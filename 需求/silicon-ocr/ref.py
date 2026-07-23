# 安装包：pip install bce-python-sdk
import copy
import json
from baidubce.auth import bce_credentials
from baidubce import bce_base_client, bce_client_configuration

AK = ""
SK = ""
ENDPOINT = "https://iam.bj.baidubce.com"

class Sample(bce_base_client.BceBaseClient):

    def __init__(self, config):
        self.config = copy.deepcopy(bce_client_configuration.DEFAULT_CONFIG)
        self.config.merge_non_none_values(config)

    def run(self):
        path = b'/v1/BCE-BEARER/token'
        headers = {
            b'Content-Type': 'application/json'
        }
        

        params = {"expireInSeconds":""}
        payload = json.dumps({}, ensure_ascii=False)
        return self._send_request(b'GET', path, headers, params, payload.encode('utf-8'))

if __name__ == '__main__':

    config = bce_client_configuration.BceClientConfiguration(credentials=bce_credentials.BceCredentials(AK, SK),
                                                                endpoint=ENDPOINT)
    client = Sample(config)
    res = client.run()
    print(res.__dict__)
