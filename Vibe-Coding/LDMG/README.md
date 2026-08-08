**LDMG** - Lite Docker Manger Gram

- 1、起因最近很多镜像升级速度比较频繁，每次都要进ssh ，进目录， down pull up
- 2、所以用AI写了个脚本只要运行 ./docker-upgrade.sh
- 3、通过2大大方便了升级流程，但是又感觉和TG互动会更方便升级镜像。

**一定要制定docker compose所在的目录，或者说只适合docker compose容器都在一个目录下**
例如：
/docker
 - emby
   - docker-compose.yml
 - openlist
   - docker-compose.yml

备注：本AI项目只测试了docker compose镜像管理。
