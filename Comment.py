import requests
import base64
import math
import random
import binascii
import sys
import pymongo
import time

from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from lxml import etree

from config import *


class Comments:

    def __init__(self):
        self.first_param = '{"rid":"R_SO_4_%s","offset":"0","total":"true","limit":"20","csrf_token":""}'
        self.second_param = "010001"
        self.third_param = "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
        self.forth_param = "0CoJUm6Qyw8W8jud"
        self.song_comment_url = "https://music.163.com/weapi/v1/resource/comments/R_SO_4_{}"
        self.song_playlist_url = "https://music.163.com/playlist?id={}"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.103 Safari/537.36",
            "Host": "music.163.com",
        }
        self.client = pymongo.MongoClient(MONGO_HOST)
        self.db = self.client[MONGO_DB]

    def randChar(self, num):
        """
        随机生成字符
        :param num: 要生成字符个数
        :return: 随机字符
        """
        template = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        result = ""
        for i in range(num):
            tmp = math.floor(random.random() * len(template))
            result += template[tmp]
        return result

    def aes_encrypt(self, text, key):
        """
        aes加密
        :param text: 待加密文本
        :param key: 密钥
        :return: aes密文
        """
        iv = "0102030405060708"
        pad = 16 - len(text) % 16
        text = text + pad * chr(pad)

        encryptor = AES.new(key, AES.MODE_CBC, iv)
        ciphertext = encryptor.encrypt(text)
        ciphertext = base64.b64encode(ciphertext)
        return ciphertext

    def rsa_encrypt(self, pubKey, text, modules):
        """
        rsa加密
        :param pubKey: 公钥
        :param text: 待加密文本
        :param modules:
        :return: rsa密文
        """
        # text = text[::-1]
        # 这是rsa加密原理，但是运行有点慢，直接用已经封装好了的库效率更高
        # rs = int(codecs.encode(text.encode('utf-8'), 'hex_codec'), 16) ** int(pubKey, 16) % int(modules, 16)
        # return format(rs, 'x').zfill(256)
        reverse_text = text[::-1]
        pub_key = RSA.construct([int(modules, 16), int(pubKey, 16)])
        encrypt_text = pub_key.encrypt(int(binascii.hexlify(reverse_text.encode('utf-8')), 16),
                                       None)[0]
        return format(encrypt_text, 'x').zfill(256)

    def get_params(self, first_param):
        """
        获取加密 params 和 encSecKey
        :return: json格式的params和encSecKey结果
        """
        i = self.randChar(16)
        encText = self.aes_encrypt(first_param, self.forth_param).decode('utf-8')
        encText = self.aes_encrypt(encText, i).decode('utf-8')
        encSecKey = self.rsa_encrypt(self.second_param, i, self.third_param)
        return {
            "encText": encText,
            "encSecKey": encSecKey
        }

    def get_playlist(self, playlist):
        """
        获取歌单所有歌曲id和歌曲名
        :param playlist: 歌单id
        :return:zip(歌曲名，歌曲ID)
        """
        resp = requests.get(self.song_playlist_url.format(playlist), headers=self.headers)
        if resp.status_code != requests.codes.OK:
            print("获取歌单失败!")
            sys.exit()
        html = etree.HTML(resp.text)
        # 因为网易云歌单里每歌单里包含歌曲的div的id不是固定的，auto-id后面是一串随机字符，所以这里用到了xpath的正则用法
        # html.xpath(r'//div[re:match(@id, "auto-id-*")]/@id', namespaces={"re":"http://exslt.org/regular-expressions"})
        lis = html.xpath('//div[@id="song-list-pre-cache"]/ul/li')
        song_names, song_ids = [], []
        for li in lis:
            id_ = li.xpath('a/@href')[0].split('=')[-1]
            name = li.xpath('a/text()')[0]
            song_ids.append(id_)
            song_names.append(name)
        return zip(song_names, song_ids)

    def get_hot_comment_from_playlist(self, playlist):
        """
        从歌单获取每首歌的热评
        :param playlist: 歌单id
        :return:
        """
        songs = self.get_playlist(playlist)
        for name, id in songs:
            params = self.get_params(self.first_param % id)
            data = {
                "params": params['encText'],
                "encSecKey": params['encSecKey']
            }
            resp = requests.post(self.song_comment_url.format(id), data=data, headers=self.headers)
            if resp.status_code != requests.codes.OK:
                print("歌曲:{name}，ID:{id}.评论获取失败".format(name=name, id=id))
                continue

            j = resp.json()
            if j['code'] != 200:
                print("歌曲:{name}，ID:{id}.评论获取失败，code:{code}".format(name=name, id=id, code=j['code']))
                continue
            for comment in j['hotComments']:
                self.save_to_mongo(name, {
                    '评论': comment['content'],
                    '赞数': comment['likeCount'],
                    '时间': time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(str(comment['time'])[:-3]))),
                    '用户名': comment['user']['nickname'],
                    '用户ID': comment['user']['userId'],
                })

    def save_to_mongo(self, song_name, content):
        """
        保存数据到MongoDB
        :param song_name:
        :param content:
        :return:
        """
        try:
            if self.db[song_name].insert_one(content):
                print('存储到MongoDB成功!')
        except Exception:
            print('存储到MongoDB失败.')


def main():
    nmc = Comments()
    nmc.get_hot_comment_from_playlist(PLAYLIST_ID)


if __name__ == '__main__':
    main()
